"""Tests for ImageCompressionCache — LFU disk cache for resized wallpapers."""

import json
import logging
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from wallpaper_auto.image_cache import (
    ImageCompressionCache,
    _CacheKey,
)


def _small_img(w: int = 10, h: int = 10, r: int = 255, g: int = 0, b: int = 0) -> Image.Image:
    return Image.new("RGB", (w, h), (r, g, b))


class TestCacheKey:
    def test_filename_format(self):
        key = _CacheKey(
            source_path="/a/b.jpg", source_size=100, source_mtime=1000.0,
            content_prefix_hash="abc", region_w=1920, region_h=1080,
        )
        name = key.filename()
        assert name.endswith("_1920x1080.png")
        assert len(name) == 30

    def test_deterministic(self):
        key = _CacheKey(
            "/a/b.jpg", 100, 1000.0, "abc", 1920, 1080,
        )
        assert key.filename() == key.filename()

    def test_different_resolution_different_filename(self):
        a = _CacheKey("/a/b.jpg", 100, 1000.0, "abc", 1920, 1080).filename()
        b = _CacheKey("/a/b.jpg", 100, 1000.0, "abc", 2560, 1440).filename()
        assert a != b

    def test_different_source_different_filename(self):
        a = _CacheKey("/a.jpg", 100, 1.0, "h1", 1920, 1080).filename()
        b = _CacheKey("/b.jpg", 100, 1.0, "h1", 1920, 1080).filename()
        assert a != b


class TestImageCompressionCacheInit:
    def test_empty_cache_dir(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        assert cache._entries == {}
        assert cache._current_size_bytes == 0

    def test_recounts_on_corrupt_index(self, tmp_path: Path, caplog):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "source.png"
        _small_img().save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        # Corrupt the index.
        (tmp_path / "resized" / "index.json").write_text("{corrupt", encoding="utf-8")

        caplog.set_level(logging.WARNING)
        cache2 = ImageCompressionCache()
        cache2.init(tmp_path)
        assert "Corrupt" in caplog.text
        # Should recount — at least the .png file should be detected.
        assert cache2._current_size_bytes > 0

    def test_removes_orphan_on_init(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "source.png"
        _small_img().save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        filename = next(iter(cache._entries))

        # Delete the source file.
        src.unlink()

        # Re-init — orphan should be removed since source is gone.
        cache2 = ImageCompressionCache()
        cache2.init(tmp_path)
        assert filename not in cache2._entries


class TestImageCompressionCacheGetPut:
    def test_get_returns_none_on_empty_cache(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img().save(src)
        assert cache.get(src, 20, 20) is None

    def test_put_then_get_returns_same_image(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(30, 30).save(src)
        expected = _small_img(20, 20, r=100)
        cache.put(src, 20, 20, expected)
        result = cache.get(src, 20, 20)
        assert result is not None
        assert result.size == (20, 20)

    def test_get_returns_none_after_source_change(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        assert cache.get(src, 20, 20) is not None

        # Modify the source file.
        _small_img(10, 10, r=200).save(src)
        # The size and mtime changed, so the key should differ.
        assert cache.get(src, 20, 20) is None

    def test_stale_cache_file_removed_on_miss(self, tmp_path: Path):
        """If the cached .png file is manually deleted, get() should remove the index entry."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
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
    def test_render_resizes_on_miss(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(100, 100).save(src)

        img = cache.render(src, 20, 20)
        assert img.size == (20, 20)

    def test_render_returns_cached_on_subsequent_call(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(100, 100).save(src)

        cache.render(src, 20, 20)
        cache.render(src, 20, 20)
        filename = next(iter(cache._entries))
        # get() hit increments access_count to 2 after put set it to 1.
        assert cache._entries[filename]["access_count"] == 2

    def test_render_different_resolution_misses(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(100, 100).save(src)

        cache.render(src, 20, 20)
        cache.render(src, 30, 30)
        assert len(cache._entries) == 2

    def test_render_missing_source_returns_blank(self, tmp_path: Path):
        """render() should return a blank fallback when the source is missing."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        missing = tmp_path / "nonexistent.png"
        img = cache.render(missing, 20, 20)
        assert img.size == (20, 20)
        # Should be all black (OSError fallback).
        assert img.getpixel((0, 0)) == (0, 0, 0)


class TestImageCompressionCacheEviction:
    def test_evict_oldest_when_over_limit(self, tmp_path: Path):
        """Put entries until eviction kicks in."""
        cache = ImageCompressionCache()
        cache.init(tmp_path, max_size_bytes=2000, evict_ratio=0.5)

        # Use a separate source file per put so keys differ naturally.
        for i in range(20):
            s = tmp_path / f"src{i}.png"
            _small_img(10, 10).save(s)
            cache.put(s, 100, 100, _small_img(100, 100, r=i * 10))

        # At most ~2 entries fit (limit 2000, target 1000, each ~500 bytes).
        assert len(cache._entries) < 10

    def test_exclude_current_from_eviction(self, tmp_path: Path):
        """The just-saved entry should survive eviction."""
        cache = ImageCompressionCache()
        cache.init(tmp_path, max_size_bytes=400)

        # Put two entries with separate source files.
        s1 = tmp_path / "src1.png"
        s2 = tmp_path / "src2.png"
        _small_img(10, 10).save(s1)
        _small_img(10, 10).save(s2)

        # Each entry is ~200 bytes. After the second put, total > 400 → eviction.
        cache.put(s1, 80, 80, _small_img(80, 80))
        cache.put(s2, 80, 80, _small_img(80, 80))

        # s2 (just saved) should survive; s1 may be evicted.
        assert len(cache._entries) >= 1
        # Verify s2's entry exists (check via public API).
        assert cache.get(s2, 80, 80) is not None

    def test_lfu_protects_frequent_entries(self, tmp_path: Path):
        """Frequently accessed entries should survive eviction over infrequent ones."""
        cache = ImageCompressionCache()
        cache.init(tmp_path, max_size_bytes=3000)

        # Create two source files with different paths.
        src_freq = tmp_path / "freq.png"
        src_infreq = tmp_path / "infreq.png"
        _small_img(10, 10).save(src_freq)
        _small_img(10, 10).save(src_infreq)

        # Put and frequently access one entry.
        cache.put(src_freq, 100, 100, _small_img(100, 100))
        for _ in range(5):
            cache.get(src_freq, 100, 100)

        # Put and lightly access another.
        cache.put(src_infreq, 100, 100, _small_img(100, 100))
        cache.get(src_infreq, 100, 100)

        # Fill until eviction.
        for i in range(15):
            s = tmp_path / f"fill{i}.png"
            _small_img(10, 10).save(s)
            cache.put(s, 100, 100, _small_img(100, 100))

        # Frequent entry should survive; infrequent may be evicted.
        assert cache.get(src_freq, 100, 100) is not None, \
            "frequent entry should survive eviction"

    def test_ageing_halves_counters(self, tmp_path: Path):
        """When any entry exceeds LFU_AGING_THRESHOLD, all counters should halve."""
        with patch("wallpaper_auto.image_cache.LFU_AGING_THRESHOLD", 5):
            cache = ImageCompressionCache()
            cache.init(tmp_path)
            src = tmp_path / "src.png"
            _small_img(10, 10).save(src)
            cache.put(src, 20, 20, _small_img(20, 20))
            filename = next(iter(cache._entries))

            # Access 6 times (triggers aging at >5).
            for _ in range(6):
                cache.get(src, 20, 20)

            # After 6 gets on top of put (count=1), aging halves 7→3.
            count = cache._entries[filename]["access_count"]
            assert count in (3, 4), f"expected ~3, got {count}"


class TestImageCompressionCacheClear:
    def test_clear_removes_all_entries(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        for i in range(3):
            s = tmp_path / f"src{i}.png"
            _small_img(10, 10).save(s)
            cache.put(s, 20, 20, _small_img(20, 20))

        count = cache.clear()
        assert count >= 3
        assert not (tmp_path / "resized" / "index.json").exists()
        # Verify via public API.
        assert cache.get(tmp_path / "src0.png", 20, 20) is None

    def test_clear_on_empty_cache(self, tmp_path: Path):
        """clear() on an empty cache returns 0 and doesn't crash."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        count = cache.clear()
        assert count == 0


class TestImageCompressionCacheWarning:
    def test_warning_on_file_exceeding_max(self, tmp_path: Path, caplog):
        """Log a warning when a single cached file exceeds max_size_bytes."""
        cache = ImageCompressionCache()
        cache.init(tmp_path, max_size_bytes=1)  # 1 byte
        src = tmp_path / "src.png"
        _small_img(100, 100).save(src)

        caplog.set_level(logging.WARNING)
        cache.put(src, 100, 100, _small_img(100, 100))

        assert "exceeds max size" in caplog.text


class TestImageCompressionCacheIndexPersistence:
    def test_index_contains_entries_after_put(self, tmp_path: Path):
        """Index file should contain entry metadata after a put()."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        index_path = tmp_path / "resized" / "index.json"
        assert index_path.exists()
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert len(data["entries"]) == 1

    def test_index_survives_restart(self, tmp_path: Path):
        """Entries added before restart should be available after restart."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20, r=50))

        # Simulate restart and verify the entry still loads.
        cache2 = ImageCompressionCache()
        cache2.init(tmp_path)
        assert cache2.get(src, 20, 20) is not None


class TestImageCompressionCacheEdgeCases:
    def test_source_file_not_found(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        missing = tmp_path / "nonexistent.png"
        assert cache.get(missing, 20, 20) is None
        cache.put(missing, 20, 20, _small_img(20, 20))  # should not crash

    def test_recovers_from_deleted_cache_dir(self, tmp_path: Path):
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))

        # Manually delete the resized dir.
        import shutil
        shutil.rmtree(tmp_path / "resized")

        # New cache should start fresh.
        cache2 = ImageCompressionCache()
        cache2.init(tmp_path)
        assert cache2.get(src, 20, 20) is None

    def test_different_styles_same_resolution_share_entry(self, tmp_path: Path):
        """Since style is not in the key, same source+resolution uses one entry."""
        cache = ImageCompressionCache()
        cache.init(tmp_path)
        src = tmp_path / "src.png"
        _small_img(10, 10).save(src)
        cache.put(src, 20, 20, _small_img(20, 20))
        cache.put(src, 20, 20, _small_img(20, 20))
        assert len(cache._entries) == 1
