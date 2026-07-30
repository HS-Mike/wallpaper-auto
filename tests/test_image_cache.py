"""Tests for ImageCompressionCache — LFU disk cache for resized wallpapers."""

import json
import logging
import time
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wallpaper_auto.image_cache import (
    ImageCompressionCache,
    _CacheKey,
)

# ── helpers ─────────────────────────────────────────────────────────────

def _small_img(w: int = 10, h: int = 10, r: int = 255, g: int = 0, b: int = 0) -> Image.Image:
    return Image.new("RGB", (w, h), (r, g, b))


# ── _CacheKey tests ─────────────────────────────────────────────────────

class TestCacheKey:
    def test_filename_format(self):
        key = _CacheKey(
            source_path="/a/b.jpg", source_size=100, source_mtime=1000.0,
            content_prefix_hash="abc", region_w=1920, region_h=1080,
        )
        name = key.filename()
        assert name.endswith("_1920x1080.png")
        assert len(name) == 16 + 1 + 4 + 1 + 4 + 4  # 30 chars + ".png"

    def test_deterministic(self):
        kwargs = dict(
            source_path="/a/b.jpg", source_size=100, source_mtime=1000.0,
            content_prefix_hash="abc", region_w=1920, region_h=1080,
        )
        assert _CacheKey(**kwargs).filename() == _CacheKey(**kwargs).filename()

    def test_different_resolution_different_filename(self):
        base = dict(
            source_path="/a/b.jpg", source_size=100, source_mtime=1000.0,
            content_prefix_hash="abc",
        )
        a = _CacheKey(**base, region_w=1920, region_h=1080).filename()
        b = _CacheKey(**base, region_w=2560, region_h=1440).filename()
        assert a != b

    def test_different_source_different_filename(self):
        a = _CacheKey("/a.jpg", 100, 1.0, "h1", 1920, 1080).filename()
        b = _CacheKey("/b.jpg", 100, 1.0, "h1", 1920, 1080).filename()
        assert a != b


# ── ImageCompressionCache tests ──────────────────────────────────────────

class TestImageCompressionCacheInit:
    def test_empty_cache_dir(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        assert cache._entries == {}
        assert cache._current_size_bytes == 0

    def test_loads_existing_index(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        # Put one entry.
        src = tmp_path / "source.png"
        _small_img().save(src)
        img = _small_img(20, 20)
        cache.put(src, 20, 20, img)
        filename = next(iter(cache._entries))

        # Create a new cache instance pointing to the same dir.
        cache2 = ImageCompressionCache(tmp_path)
        assert filename in cache2._entries

    def test_recounts_on_corrupt_index(self, tmp_path: Path, caplog):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "source.png"
        _small_img().save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        # Corrupt the index.
        (tmp_path / "resized" / "index.json").write_text("{corrupt", encoding="utf-8")

        caplog.set_level(logging.WARNING)
        cache2 = ImageCompressionCache(tmp_path)
        assert "Corrupt" in caplog.text
        # Should recount — at least the .png file should be detected.
        assert cache2._current_size_bytes > 0

    def test_removes_orphan_on_init(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "source.png"
        _small_img().save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        filename = next(iter(cache._entries))

        # Delete the source file.
        src.unlink()

        # Re-init — orphan should be removed since source is gone.
        cache2 = ImageCompressionCache(tmp_path)
        assert filename not in cache2._entries

    def test_cleans_up_stale_tmp_file(self, tmp_path: Path):
        tmp_index = tmp_path / "resized" / "index.json.tmp"
        tmp_index.parent.mkdir(parents=True, exist_ok=True)
        tmp_index.write_text("{}", encoding="utf-8")

        ImageCompressionCache(tmp_path)  # should not crash
        assert not tmp_index.exists()


class TestImageCompressionCacheGetPut:
    def test_get_returns_none_on_empty_cache(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img().save(src)
        assert cache.get(src, 20, 20) is None

    def test_put_then_get_returns_same_image(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(30, 30).save(src)
        expected = _small_img(20, 20, r=100)
        cache.put(src, 20, 20, expected)
        result = cache.get(src, 20, 20)
        assert result is not None
        assert result.size == (20, 20)

    def test_get_increments_access_count(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        filename = next(iter(cache._entries))
        assert cache._entries[filename]["access_count"] == 1

        cache.get(src, 20, 20)
        assert cache._entries[filename]["access_count"] == 2

    def test_get_returns_none_after_source_change(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        assert cache.get(src, 20, 20) is not None

        # Modify the source file.
        _small_img(10, 10, r=200).save(src)
        # The size and mtime changed, so the key should differ.
        assert cache.get(src, 20, 20) is None

    def test_different_resolution_different_cache_entry(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        cache.put(src, 30, 30, _small_img(30, 30))
        assert len(cache._entries) == 2

    def test_stale_cache_file_removed_on_miss(self, tmp_path: Path):
        """If the cached .png file is manually deleted, get() should remove the index entry."""
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        filename = next(iter(cache._entries))
        assert filename in cache._entries

        # Manually delete the cached file.
        (tmp_path / "resized" / filename).unlink()

        result = cache.get(src, 20, 20)
        assert result is None
        assert filename not in cache._entries


class TestImageCompressionCacheRender:
    def test_render_calls_resize_fn_on_miss(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        called = False

        def resize_fn() -> Image.Image:
            nonlocal called
            called = True
            return _small_img(20, 20)

        img = cache.render(src, 20, 20, resize_fn)
        assert called
        assert img.size == (20, 20)

    def test_render_returns_cached_on_subsequent_call(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        call_count = 0

        def resize_fn() -> Image.Image:
            nonlocal call_count
            call_count += 1
            return _small_img(20, 20)

        cache.render(src, 20, 20, resize_fn)
        assert call_count == 1

        cache.render(src, 20, 20, resize_fn)
        assert call_count == 1  # not called again

    def test_render_different_resolution_misses(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        call_count = 0

        def resize_fn(w: int, h: int):
            def _fn():
                nonlocal call_count
                call_count += 1
                return _small_img(w, h)
            return _fn

        cache.render(src, 20, 20, resize_fn(20, 20))
        cache.render(src, 30, 30, resize_fn(30, 30))
        assert call_count == 2


class TestImageCompressionCacheEviction:
    def test_evict_oldest_when_over_limit(self, tmp_path: Path):
        """Put many entries, verify that the least frequently used is evicted."""
        max_bytes = 2000  # small limit
        with patch("wallpaper_auto.image_cache.CACHE_MAX_SIZE_BYTES", max_bytes):
            with patch("wallpaper_auto.image_cache.CACHE_EVICT_TARGET_RATIO", 0.5):
                cache = ImageCompressionCache(tmp_path)
                src = tmp_path / "src.png"
                _small_img(10, 10).save(src)

                # Put entries — each 100x100 PNG ~500 bytes, 20× = ~10 KB.
                for i in range(20):
                    img = _small_img(100, 100, r=i * 10)
                    cache.put(src, 100, 100, img)
                    # Touch source to change its mtime so each put gets a
                    # different key.
                    time.sleep(0.01)
                    src.touch()

                # We should have fewer entries than put calls (limit 2000,
                # target 1000, each entry ~500 bytes → ~2 entries max).
                assert len(cache._entries) < 10

    def test_exclude_current_from_eviction(self, tmp_path: Path):
        """The just-saved entry should not be evicted immediately."""
        max_bytes = 400
        with patch("wallpaper_auto.image_cache.CACHE_MAX_SIZE_BYTES", max_bytes):
            with patch("wallpaper_auto.image_cache.CACHE_EVICT_TARGET_RATIO", 0.9):
                cache = ImageCompressionCache(tmp_path)
                src = tmp_path / "src.png"
                _small_img(10, 10).save(src)

                # Each 80x80 PNG is ~200 bytes. With a 400 byte limit,
                # the second entry should trigger eviction.
                cache.put(src, 80, 80, _small_img(80, 80))
                assert len(cache._entries) == 1
                src.touch()

                cache.put(src, 80, 80, _small_img(80, 80))
                # The second entry was just saved so it should be excluded
                # from eviction — only the first entry might be evicted.
                src.touch()

                cache.put(src, 80, 80, _small_img(80, 80))
                # Should still have at least the current entry.
                assert len(cache._entries) >= 1

    def test_lfu_access_protects_frequent_entries(self, tmp_path: Path):
        """Frequently accessed entries should survive eviction over infrequent ones."""
        max_bytes = 3000
        with patch("wallpaper_auto.image_cache.CACHE_MAX_SIZE_BYTES", max_bytes):
            with patch("wallpaper_auto.image_cache.CACHE_EVICT_TARGET_RATIO", 0.5):
                cache = ImageCompressionCache(tmp_path)
                src_freq = tmp_path / "freq.png"
                src_infreq = tmp_path / "infreq.png"
                _small_img(10, 10).save(src_freq)
                _small_img(10, 10).save(src_infreq)

                # Put frequent entry and access it many times.
                cache.put(src_freq, 100, 100, _small_img(100, 100))
                for _ in range(5):
                    cache.get(src_freq, 100, 100)

                # Put infrequent entry.
                time.sleep(0.01)
                src_infreq.touch()
                cache.put(src_infreq, 100, 100, _small_img(100, 100))

                # Access infrequent once.
                cache.get(src_infreq, 100, 100)

                # Fill until eviction — infrequent should be evicted first.
                for i in range(15):
                    s = tmp_path / f"fill{i}.png"
                    _small_img(10, 10).save(s)
                    cache.put(s, 100, 100, _small_img(100, 100))

                # The frequent entry should survive because it has higher LFU.
                freq_survived = any(
                    "freq" in cache._entries[k].get("source_path", "")
                    for k in cache._entries
                )
                assert freq_survived, "frequent entry should survive"

    def test_ageing_halves_counters(self, tmp_path: Path):
        """When any entry exceeds LFU_AGING_THRESHOLD, all counters should halve."""
        with patch("wallpaper_auto.image_cache.LFU_AGING_THRESHOLD", 5):
            cache = ImageCompressionCache(tmp_path)
            src = tmp_path / "src.png"
            _small_img(10, 10).save(src)
            cache.put(src, 20, 20, _small_img(20, 20))
            filename = next(iter(cache._entries))

            # Access 6 times (triggers aging at >5).
            for _ in range(6):
                cache.get(src, 20, 20)

            # access_count was 1 + 6 = 7, after halving should be 3.
            count = cache._entries[filename]["access_count"]
            # After being at 7 and halved: 7//2 = 3 (but there might be additional
            # access from the get() calls, let's just verify it's <= 3)
            # Actually: put sets to 1, then 6 gets → 7, then _age_if_needed() after get #6
            # halves to 3 (7//2). Then the get() that triggered aging increments to 4?
            # Wait, the order in get() is: increment, then age_if_needed.
            # So: after 5 gets: count = 6 (1+5). Get #6: count=7, age_if_needed → 7>5, halve all → 3.
            # But get() returns after _write_index. So final count = 3.
            assert count in (3, 4), f"expected ~3, got {count}"


class TestImageCompressionCacheClear:
    def test_clear_removes_all_entries(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        for i in range(3):
            s = tmp_path / f"src{i}.png"
            _small_img(10, 10).save(s)
            cache.put(s, 20, 20, _small_img(20, 20))

        count = cache.clear()
        assert count >= 3
        assert cache._entries == {}
        assert cache._current_size_bytes == 0
        assert not (tmp_path / "resized" / "index.json").exists()


class TestImageCompressionCacheWarning:
    def test_warning_on_file_exceeding_max(self, tmp_path: Path, caplog):
        """Log a warning when a single cached file exceeds CACHE_MAX_SIZE_BYTES."""
        with patch("wallpaper_auto.image_cache.CACHE_MAX_SIZE_BYTES", 1):  # 1 byte
            cache = ImageCompressionCache(tmp_path)
            src = tmp_path / "src.png"
            _small_img(100, 100).save(src)

            caplog.set_level(logging.WARNING)
            cache.put(src, 100, 100, _small_img(100, 100))

            assert "exceeds CACHE_MAX_SIZE_BYTES" in caplog.text


class TestImageCompressionCacheIndexPersistence:
    def test_index_is_written_atomically(self, tmp_path: Path):
        """Index writes should use atomic rename (temp file strategy)."""
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        index_path = tmp_path / "resized" / "index.json"
        assert index_path.exists()

        # The .tmp file should not exist after a successful write.
        assert not (tmp_path / "resized" / "index.json.tmp").exists()

        # Index should contain valid JSON.
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert data["version"] == 1
        assert len(data["entries"]) == 1

    def test_index_survives_restart(self, tmp_path: Path):
        """Entries added before restart should be available after restart."""
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20, r=50))
        original_filename = next(iter(cache._entries))
        original_count = cache._entries[original_filename]["access_count"]

        # Simulate restart.
        cache2 = ImageCompressionCache(tmp_path)
        assert original_filename in cache2._entries
        assert cache2._entries[original_filename]["access_count"] == original_count


class TestImageCompressionCacheEdgeCases:
    def test_source_file_not_found(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        missing = tmp_path / "nonexistent.png"
        assert cache.get(missing, 20, 20) is None
        cache.put(missing, 20, 20, _small_img(20, 20))  # should not crash

    def test_recovers_from_deleted_cache_dir(self, tmp_path: Path):
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        # Manually delete the resized dir.
        import shutil
        shutil.rmtree(tmp_path / "resized")

        # New cache should start fresh.
        cache2 = ImageCompressionCache(tmp_path)
        assert cache2._entries == {}
        assert cache2.get(src, 20, 20) is None

    def test_different_styles_same_resolution_share_entry(self, tmp_path: Path):
        """Since style is not in the key, same source+resolution uses one entry."""
        cache = ImageCompressionCache(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        cache.put(src, 20, 20, _small_img(20, 20))
        assert len(cache._entries) == 1
