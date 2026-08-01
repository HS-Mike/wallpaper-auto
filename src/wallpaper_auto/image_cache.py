"""
Disk-backed cache for display-resolution wallpaper images.

Caches source images resized to a target display resolution so that
expensive LANCZOS resizing (especially for 200 MB+ source files) is
only done once per unique (source, resolution) pair.

Eviction uses LFU (Least Frequently Used) with an on-disk ``index.json``
that persists access counts across application restarts.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TypedDict

from PIL import Image


class _CacheEntry(TypedDict):
    """Schema for each entry in the LFU index."""
    access_count: int
    source_path: str
    source_size: int
    source_mtime: float
    content_prefix_hash: str
    file_size: int

logger = logging.getLogger(__name__)


CACHE_CONTENT_HASH_BYTES: int = 65536          # 64 KB
CACHE_MAX_SIZE_BYTES: int = 200 * 1024 * 1024  # 200 MB
CACHE_EVICT_TARGET_RATIO: float = 0.9          # evict until 90 % of max
LFU_AGING_THRESHOLD: int = 100                 # halve all counters at this ceiling


def _resize_image(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize *img* to ``(w, h)`` using high-quality LANCZOS."""
    return img.resize((w, h), Image.Resampling.LANCZOS, reducing_gap=3)


@dataclass(frozen=True)
class _CacheKey:
    """Uniquely identifies a cacheable image at a target resolution."""
    source_path: str
    source_size: int
    source_mtime: float
    content_prefix_hash: str
    region_w: int
    region_h: int

    def filename(self) -> str:
        """Return e.g. ``a1b2c3d4e5f6a7b8_1920x1080.png``."""
        raw = (
            f"{self.source_path}\x00{self.source_size}\x00"
            f"{self.source_mtime}\x00{self.content_prefix_hash}\x00"
            f"{self.region_w}\x00{self.region_h}"
        )
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        return f"{digest}_{self.region_w}x{self.region_h}.png"


class ImageCompressionCache:
    """Persistent LFU cache for display-resolution wallpaper images.

    Stores resized images as PNG files under ``<cache_dir>/resized/``
    and keeps access-frequency metadata in ``index.json`` within the
    same directory.
    """

    _SUBDIR = "resized"
    _INDEX_FILE = "index.json"

    def __init__(
        self,
        cache_dir: Path,
        max_size_bytes: int | None = None,
        evict_ratio: float | None = None,
    ) -> None:
        self._cache_dir = cache_dir / self._SUBDIR
        if not self._cache_dir.exists():
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            logger.info("Created cache directory: %s", self._cache_dir)
        self._lock = threading.Lock()
        self._index_path = self._cache_dir / self._INDEX_FILE

        self._max_size_bytes = (
            max_size_bytes if max_size_bytes is not None else CACHE_MAX_SIZE_BYTES
        )
        self._evict_ratio = (
            evict_ratio if evict_ratio is not None else CACHE_EVICT_TARGET_RATIO
        )

        # In-memory state
        self._entries: dict[str, _CacheEntry] = {}

        self._load_index()

    @property
    def _current_size_bytes(self) -> int:
        """Compute current cache size from entry metadata."""
        return sum(e["file_size"] for e in self._entries.values())

    def get(
        self, source_path: Path, region_w: int, region_h: int
    ) -> Image.Image | None:
        """Return cached resized image or ``None`` on miss.

        Increments the LFU access counter on hit.
        """
        key = self._compute_key(source_path, region_w, region_h)
        if key is None:
            return None

        filename = key.filename()
        with self._lock:
            entry = self._entries.get(filename)
            if entry is None:
                return None

            cached_path = self._cache_dir / filename
            if not cached_path.is_file():
                # Stale index entry — clean up.
                del self._entries[filename]
                self._write_index()
                return None

            # LFU: increment access count.
            entry["access_count"] += 1
            self._age_if_needed()
            self._write_index()

        try:
            return Image.open(cached_path)
        except OSError:
            logger.exception("Failed to read cached image %s", cached_path)
            return None

    def put(
        self,
        source_path: Path,
        region_w: int,
        region_h: int,
        resized: Image.Image,
    ) -> None:
        """Store a resized image.  Evicts LFU entries when over the size limit.

        If the single file itself exceeds the configured max size a warning
        is logged; the file is still saved and counted, but it is excluded
        from eviction and does not trigger an eviction pass on its own.
        """
        key = self._compute_key(source_path, region_w, region_h)
        if key is None:
            return

        filename = key.filename()
        cached_path = self._cache_dir / filename

        # Save to disk.
        try:
            resized.save(cached_path, "PNG")
        except OSError:
            logger.exception("Failed to write cached image %s", cached_path)
            return
        file_size = cached_path.stat().st_size

        if file_size > self._max_size_bytes:
            logger.warning(
                "Cached image %s (%d bytes) exceeds max size (%d)",
                filename, file_size, self._max_size_bytes,
            )

        with self._lock:
            self._entries[filename] = _CacheEntry(
                access_count=1,
                source_path=key.source_path,
                source_size=key.source_size,
                source_mtime=key.source_mtime,
                content_prefix_hash=key.content_prefix_hash,
                file_size=file_size,
            )
            if (
                file_size <= self._max_size_bytes
                and self._current_size_bytes > self._max_size_bytes
            ):
                self._evict(exclude={filename})
            self._write_index()

    def render(
        self, source_path: Path, region_w: int, region_h: int
    ) -> Image.Image:
        """Return a cached resized image, or compute and cache it.

        This is the primary entry point — the caller says
        "render this source for this display" and gets the result
        regardless of cache state.
        """
        cached = self.get(source_path, region_w, region_h)
        if cached is not None:
            return cached

        try:
            with Image.open(source_path) as img:
                resized = _resize_image(img, region_w, region_h)
        except OSError:
            logger.exception("Cache failed to open/resize %s", source_path)
            # Return a blank image so the composite can still proceed.
            return Image.new("RGB", (region_w, region_h), (0, 0, 0))

        self.put(source_path, region_w, region_h, resized)
        return resized

    def clear(self) -> int:
        """Delete all cached PNG files and the index.

        Returns the number of files removed.
        """
        with self._lock:
            count = 0
            if self._cache_dir.is_dir():
                for p in self._cache_dir.iterdir():
                    if p.suffix == ".png":
                        p.unlink(missing_ok=True)
                        count += 1
                index = self._cache_dir / self._INDEX_FILE
                if index.exists():
                    index.unlink(missing_ok=True)
            self._entries.clear()
        return count

    def _compute_key(
        self, source_path: Path, region_w: int, region_h: int,
    ) -> _CacheKey | None:
        """Build a ``_CacheKey`` from ``stat()`` and a content prefix hash.

        Returns ``None`` if the source file cannot be stat'd.
        """
        try:
            st = source_path.stat()
        except OSError:
            logger.warning("Cannot stat source file %s", source_path)
            return None

        prefix_hash = self._hash_prefix(source_path, CACHE_CONTENT_HASH_BYTES)
        return _CacheKey(
            source_path=str(source_path.resolve()),
            source_size=st.st_size,
            source_mtime=st.st_mtime,
            content_prefix_hash=prefix_hash,
            region_w=region_w,
            region_h=region_h,
        )

    @staticmethod
    def _hash_prefix(source_path: Path, n_bytes: int) -> str:
        """SHA256 of the first *n_bytes* of the file (or the whole file)."""
        h = hashlib.sha256()
        try:
            with open(source_path, "rb") as f:
                chunk = f.read(n_bytes)
                h.update(chunk)
        except OSError:
            return ""
        return h.hexdigest()

    def _evict(self, exclude: set[str] | None = None) -> None:
        """Delete LFU entries until under the target size.

        Entries whose filename appears in *exclude* are never chosen
        (used to protect the just-saved entry from immediate eviction).
        """
        if exclude is None:
            exclude = set()

        target = int(self._max_size_bytes * self._evict_ratio)

        while self._current_size_bytes > target and len(self._entries) > len(exclude):
            # Find the entry with the lowest access_count (excluding protected).
            candidate: str | None = None
            candidate_count = -1
            for fname, meta in self._entries.items():
                if fname in exclude:
                    continue
                ac = meta["access_count"]
                if candidate is None or ac < candidate_count:
                    candidate = fname
                    candidate_count = ac

            if candidate is None:
                break  # only protected entries remain

            self._remove_entry(candidate)

    def _remove_entry(self, filename: str) -> None:
        """Delete a single cached file and remove it from the index."""
        cached_path = self._cache_dir / filename
        try:
            cached_path.unlink(missing_ok=True)
        except OSError:
            logger.warning("Failed to remove cache file %s", cached_path)

        self._entries.pop(filename, None)

    def _age_if_needed(self) -> None:
        """Halve all LFU counters when any entry exceeds the threshold."""
        if any(
            meta["access_count"] > LFU_AGING_THRESHOLD
            for meta in self._entries.values()
        ):
            for meta in self._entries.values():
                meta["access_count"] //= 2

    def _write_index(self) -> None:
        """Write the LFU index to ``index.json``."""
        payload = {
            "entries": self._entries,
        }
        try:
            with open(self._index_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
        except OSError:
            logger.exception("Failed to write cache index")

    def _load_index(self) -> None:
        """Read ``index.json``, validate entries, and initialise counters.

        If the file is missing or corrupt the cached PNG files are kept
        and their sizes are recounted from disk (LFU counters reset to 0).
        """
        if not self._index_path.is_file():
            self._recount_from_disk()
            return

        try:
            with open(self._index_path, encoding="utf-8") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            logger.warning("Corrupt cache index, recounting from disk")
            self._recount_from_disk()
            return

        entries = data.get("entries", {})
        if not isinstance(entries, dict):
            logger.warning("Invalid entries in cache index, recounting from disk")
            self._recount_from_disk()
            return

        validated: dict[str, _CacheEntry] = {}
        for fname, meta in entries.items():
            if not isinstance(meta, dict):
                continue
            cached_path = self._cache_dir / fname
            if not cached_path.is_file():
                continue  # orphan

            # Verify source still matches metadata.
            source_path_str = str(meta.get("source_path", ""))
            source_path = Path(source_path_str) if source_path_str else None
            if source_path:
                if source_path.is_file():
                    try:
                        st = source_path.stat()
                    except OSError:
                        st = None
                    if st is not None and (
                        st.st_size != meta.get("source_size")
                        or st.st_mtime != meta.get("source_mtime")
                    ):
                        # Source changed — this entry is stale.
                        continue
                else:
                    # Source no longer exists — orphan.
                    continue

            validated[fname] = _CacheEntry(
                access_count=int(meta.get("access_count", 0)),
                source_path=source_path_str,
                source_size=int(meta.get("source_size", 0)),
                source_mtime=float(meta.get("source_mtime", 0)),
                content_prefix_hash=str(meta.get("content_prefix_hash", "")),
                file_size=int(meta.get("file_size", 0)),
            )

        self._entries = validated

    def _recount_from_disk(self) -> None:
        """Scan the resized directory and rebuild index from scratch.

        All LFU counters are reset to 0.
        """
        entries: dict[str, _CacheEntry] = {}

        if not self._cache_dir.is_dir():
            self._entries = {}
            return

        for p in sorted(self._cache_dir.iterdir()):
            if p.suffix != ".png":
                continue
            try:
                st = p.stat()
            except OSError:
                continue
            entries[p.name] = _CacheEntry(
                access_count=0,
                source_path="",
                source_size=0,
                source_mtime=0.0,
                content_prefix_hash="",
                file_size=st.st_size,
            )

        self._entries = entries

        # Write fresh index so we don't hit the recount path again.
        if entries:
            self._write_index()
