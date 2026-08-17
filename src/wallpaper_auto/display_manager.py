"""
Per-display wallpaper manager.

Tracks per-monitor wallpaper state across multiple displays,
composites per-monitor images into a spanned wallpaper, and applies
it via the COM IDesktopWallpaper API.
"""

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from .config_store import ConfigStore
from .image_cache import ImageCompressionCache, _resize_image
from .resource.base_resource import BaseResource
from .resource.static_wallpaper import StaticWallpaper
from .util.display_utils import (
    DisplayCapability,
    DisplayId,
    DisplayInfo,
    get_display_capability,
    get_display_info,
    get_display_resolution,
    get_display_scale,
    resolve_target_resolution,
    resolve_target_scale_step,
    set_display_resolution,
    set_display_scale,
)
from .util.wallpaper_util import (
    WallpaperStyle,
    com_session,
    get_wallpaper,
    get_wallpaper_style,
    set_wallpaper,
    set_wallpaper_style,
)

logger = logging.getLogger(__name__)
logging.getLogger("PIL").setLevel(logging.WARNING)


@dataclass
class DisplayState:
    """Per-display state tracked by :class:`DisplayManager`."""

    display_id: DisplayId
    display_info: DisplayInfo
    capability: DisplayCapability
    resource: BaseResource
    is_patch: bool
    original_resolution: tuple[int, int]
    original_scale: int
    original_wallpaper: tuple[WallpaperStyle, Path] | None = None
    canvas: tuple[WallpaperStyle, Path | Image.Image] | None = None
    # Resolution/scale the app has applied; None = display still at its original
    # value. Restore bookkeeping for stop()/remove_display(), and the idempotency
    # short-circuit in the setters.
    current_resolution: tuple[int, int] | None = None
    current_scale: int | None = None
    # Resolution/scale recorded by update_display_scene() not yet applied by
    # apply_display_scene(). None = no scene pending; (None, None) still counts
    # as pending (a scene that restores both).
    pending: tuple[tuple[int, int] | None, int | None] | None = None


class DisplayManager:
    """Per-display lifecycle — wallpaper composition plus resolution/scale control.

    Displays are addressed by an opaque :data:`DisplayId` — the hash of the
    display's system-API identifiers (see :func:`_make_display_id`) — returned
    by :meth:`add_display`.  Unlike ``IDesktopWallpaper`` (which keys on
    ``monitor_device_path``), the resolution/scale APIs identify a monitor by
    ``device_name``/``adapter_id``/``source_id``; because none of those is
    guaranteed stable, a changed hash surfaces as a new display that the
    manager re-adds with fresh identifiers, capability, and originals.

    Since ``IDesktopWallpaper`` has a single global ``WallpaperStyle``,
    per-monitor styles are achieved by compositing each monitor's image
    into a spanned wallpaper canvas and applying it with ``SPAN`` style.

    Compositing happens in two stages:
    1. **Resize** — each source image is cover-resized to fill its display
       while preserving aspect ratio (no cropping). Path-based images go
       through ``ImageCompressionCache.render()``; ``CENTER``/``TILE`` styles
       keep native pixels.
    2. **Composite** — ``_render_image_for_region`` crops each resized image
       to its display's exact resolution, centered in the monitor's
       virtual-desktop region, then the canvas is applied as a ``SPAN``
       wallpaper.

    The "patch" mechanism tracks whether a display is showing its original
    (pre-app) wallpaper (``is_patch == True``) or a resource the app set.
    This ensures original wallpapers are restored when the app stops or
    a display is removed.

    Resolution changes persist to the registry (``CDS_UPDATEREGISTRY``);
    scale changes are session-only.  Either way the original values are
    recorded at :meth:`add_display`, each change snaps to a supported value
    from the cached capability, and the originals are restored on
    :meth:`stop` or when a still-connected display is removed.

    **Thread-safety:** every public method is internally synchronized by a
    single ``_lock`` (monitor pattern) — callers never rely on threading
    conventions. Two invariants keep the lock deadlock/stall-free:
      1. ``_lock`` is never held across a resource lifecycle call
         (``demount()``/``mount()``), since ``demount()`` may join a cycling
         thread blocked on the lock and ``mount()`` calls back into
         ``update_canvas()``.
      2. ``_lock`` is never held across a nested public-method call; methods
         that call other public methods compute under the lock, release it,
         then delegate.
    """

    def __init__(self) -> None:
        self._displays: dict[DisplayId, DisplayState] = {}
        self._wallpaper_applied: bool = False
        self._image_cache: ImageCompressionCache | None = None
        self._lock = threading.Lock()
        # Dedicated mutex for the time-based composite suffix, so concurrent
        # plots are never handed the same filename.
        self._composite_lock = threading.Lock()
        self._composite_last_time: int = 0

    def init_cache(
        self,
        cache_path: Path,
        resize_enabled: bool,
        max_size_bytes: int,
        evict_ratio: float,
    ) -> None:
        """Create and initialize the image compression cache.

        Called from ``WallpaperController.load_config()`` alongside the
        other managers' ``init()`` methods.  When *resize_enabled* is
        false the cache is left as ``None`` and ``_resolve_cached_image``
        opens images directly.

        Args:
            cache_path: Directory the cache is persisted under.
            resize_enabled: Whether to enable the on-disk image cache; when
                False the cache is left as ``None``.
            max_size_bytes: Soft size limit that triggers LFU eviction.
            evict_ratio: Fraction of the size limit to evict down to.
        """
        with self._lock:
            if resize_enabled:
                cache = ImageCompressionCache()
                cache.init(
                    cache_path,
                    max_size_bytes=max_size_bytes,
                    evict_ratio=evict_ratio,
                )
                self._image_cache = cache

    def start(self) -> None:
        """Begin a fresh lifecycle: record originals for all connected displays.

        A composite must never be applied before ``start()``, so
        ``_wallpaper_applied`` must already be ``False`` here (``stop()`` clears
        it at the end of the previous lifecycle).
        """
        with self._lock:
            if self._wallpaper_applied:
                raise RuntimeError("cannot start: a composite was applied before start()")
            curr_display_info = get_display_info()
            if curr_display_info is None:
                return
        for d in curr_display_info:
            self.add_display(d)

    def stop(self, restore_original: bool = True) -> None:
        """Revert all displays to their original wallpapers, resolutions, and scales.

        Restores each display that has a genuine ``original_wallpaper`` record
        (captured at ``start()``) and clears ``_wallpaper_applied`` for the next
        lifecycle.  Displays added after the app applied its SPAN composite
        have no original wallpaper record; instead of being left on the app's
        composite, they are filled with a pure black wallpaper.  Unplugged
        displays are dropped by :meth:`remove_display` and never touched here.
        Any resolution/scale the app changed is reverted to its recorded original.

        Args:
            restore_original: Whether to revert wallpapers to their originals
                (or black for displays without one); when False the current
                wallpaper — typically the at_shutdown target — survives and
                only resolution/scale is restored.
        """
        with self._lock:
            monitors = list(self._displays)
            black_targets = [
                (m, s.display_info.monitor_device_path)
                for m, s in self._displays.items()
                if s.original_wallpaper is None
            ]
        for i, m in enumerate(monitors):
            self.remove_display(m, restore_original=restore_original)
        with self._lock:
            for state in self._displays.values():
                self._restore_resolution(state)
                self._restore_scale(state)
                if restore_original and state.original_wallpaper is not None:
                    self._set_wallpaper(
                        state.original_wallpaper[0],
                        state.display_info.monitor_device_path,
                        state.original_wallpaper[1],
                    )
            if restore_original:
                for _m, monitor_device_path in black_targets:
                    self._set_wallpaper(
                        WallpaperStyle.FILL,
                        monitor_device_path,
                        self._black_wallpaper_path(),
                    )
                self._wallpaper_applied = False
        logger.info("display manager stop: done")

    def update_display(self) -> list[DisplayInfo] | None:
        """Detect monitor hotplug events and add/remove displays accordingly.

        Returns:
            Current display info list, or None if displays cannot be queried
            (the sync is skipped and state is left unchanged).
        """
        curr_display_info = get_display_info()
        if curr_display_info is None:
            return None
        curr = {d.display_id for d in curr_display_info}
        with self._lock:
            active = set(self._displays.keys())
            plugged = curr - active
            unplugged = active - curr
        for display in curr_display_info:
            if display.display_id in plugged:
                self.add_display(display)
        for display_id in unplugged:
            self.remove_display(display_id)
        return curr_display_info

    @property
    def active_display_ids(self) -> set[DisplayId]:
        """Return the set of display ids currently tracked by the manager."""
        with self._lock:
            return set(self._displays.keys())

    def add_display(self, display_info: DisplayInfo) -> DisplayId:
        """Register a newly connected display.

        Builds the display's opaque :data:`DisplayId` from its system-API
        identifiers, creates a patch ``StaticWallpaper``, and stores a genuine
        ``original_wallpaper`` record only while the desktop wallpaper is still
        untouched by the app.  Once the app has applied its SPAN composite,
        ``GetWallpaper`` ignores the monitorID and returns the app's own
        composite — a Windows ``IDesktopWallpaper`` API limitation — so no
        original wallpaper record is kept for such displays.
        The display's supported resolution/scale capability is queried and
        cached (see :func:`get_display_capability`), and the pre-modification
        resolution/scale is recorded as the original.  A capability query
        failure raises (it is never silently treated as "no capability").

        Args:
            display_info: Topology snapshot of the connected display.

        Returns:
            The opaque id assigned to the display.
        """
        display_id = display_info.display_id
        with self._lock:
            if display_id in self._displays:
                return display_id
        display_resolution = get_display_resolution(display_info.device_name)
        if display_resolution is None:
            raise RuntimeError(
                f"display resolution request fail on {display_info.model or 'UNKNOWN MODEL'} "
                f"({display_info.device_name or 'UNKNOWN DEVICE NAME'})"
            )
        display_scale = get_display_scale(display_info.device_name)
        if display_scale is None:
            raise RuntimeError(
                f"display scale request fail on {display_info.model or 'UNKNOWN MODEL'} "
                f"({display_info.device_name or 'UNKNOWN DEVICE NAME'})"
            )
        display_capability = get_display_capability(
            display_info.device_name,
            display_info.adapter_id,
            display_info.source_id,
            raise_error=True,
        )
        assert display_capability is not None
        style = get_wallpaper_style()
        image_path = get_wallpaper(display_info.monitor_device_path)
        patch = StaticWallpaper(style, image_path)
        patch._bind_display(display_info)
        display_state = DisplayState(
            display_id=display_id,
            display_info=display_info,
            capability=display_capability,
            resource=patch,
            is_patch=True,
            original_wallpaper=(style, image_path) if not self._wallpaper_applied else None,
            original_resolution=display_resolution,
            original_scale=display_scale,
        )
        with self._lock:
            self._displays[display_id] = display_state
        return display_id

    def remove_display(self, display_id: DisplayId, restore_original: bool = True) -> None:
        """Revert a display back to its original wallpaper and resolution/scale.

        Demounts the active resource, replaces it with a patch
        ``StaticWallpaper`` of the original wallpaper, and re-applies it.
        Displays with no ``original_wallpaper`` record (added after the app
        applied its composite) are dropped instead of patched — there is no
        genuine original to restore, and mounting a SPAN composite patch would
        corrupt the whole canvas.  Patch displays are always dropped: they have
        no active resource to demount.  A display with a genuine
        ``original_wallpaper`` record is only patched and restored while it is
        still physically connected; one that was unplugged is dropped (the
        composite is re-applied without it and there is no screen left to
        restore to).

        A display whose resolution/scale the app modified is restored to its
        original values in the branches where it may still be connected; a
        physically unplugged display is dropped and left to the OS to revert.

        Args:
            display_id: Opaque id of the display.
            restore_original: Whether to revert the display to its original
                wallpaper; when False the current resource is left mounted
                (used by :meth:`stop` for the at_shutdown target).
        """
        with self._lock:
            state = self._displays[display_id]
            if state.is_patch is True:
                # Patch display: nothing to demount; restore res/scale if set.
                self._restore_resolution(state)
                self._restore_scale(state)
                del self._displays[display_id]
                return
            resource_to_demount = state.resource
            demount_resource = True
            if state.original_wallpaper is None:
                # Hotplugged display with a resource: drop it; a re-plug re-adds
                # via add_display().  Restore res/scale while it may still be
                # connected (an unplugged display fails the setter harmlessly).
                self._restore_resolution(state)
                self._restore_scale(state)
                del self._displays[display_id]
                patch_to_mount = None
            else:
                displays = get_display_info()
                connected = {d.display_id for d in displays} if displays is not None else None
                if connected is None or display_id in connected:
                    # Still present (or unqueryable → conservative fallback):
                    # restore the original wallpaper and res/scale.
                    self._restore_resolution(state)
                    self._restore_scale(state)
                    if restore_original:
                        patch = StaticWallpaper(*state.original_wallpaper)
                        patch._bind_display(state.display_info)
                        state.resource = patch
                        state.is_patch = True
                        patch_to_mount = patch
                    else:
                        # Leave the current resource (e.g. at_shutdown target)
                        # mounted; do not replace it with a patch.
                        del self._displays[display_id]
                        patch_to_mount = None
                        demount_resource = False
                else:
                    # Physically unplugged: drop it — nothing to restore to.
                    del self._displays[display_id]
                    patch_to_mount = None
        if demount_resource:
            resource_to_demount.demount()  # outside lock (may join a cycling thread)
        if patch_to_mount is not None:
            patch_to_mount.mount()  # outside lock (→ update_canvas takes the lock)

    def _black_wallpaper_path(self) -> Path:
        """Return a path to a pure-black wallpaper image, creating it if needed.

        A 1x1 black PNG applied with :attr:`WallpaperStyle.FILL` renders as a
        solid black desktop.

        Returns:
            Path to a pure-black PNG image.
        """
        cache_dir = ConfigStore.instance.cache_path
        cache_dir.mkdir(parents=True, exist_ok=True)
        black_path = cache_dir / "_black.png"
        if not black_path.exists():
            Image.new("RGB", (1, 1), (0, 0, 0)).save(black_path, "PNG")
        return black_path

    def _set_wallpaper(
        self,
        style: WallpaperStyle,
        monitor_device_path: str,
        path: Path,
    ) -> None:
        """Apply a per-display wallpaper, tolerating a transient COM failure.

        Used only during shutdown, when the display topology is in flux and a
        display may have just disconnected between the last query and this COM
        call.  Such a failure is expected/transient — log and continue rather
        than crash ``stop()``.

        Args:
            style: Wallpaper fit/style to apply.
            monitor_device_path: IDesktopWallpaper monitor path of the target.
            path: Image path to set as the wallpaper.
        """
        try:
            with com_session():
                set_wallpaper_style(style)
                set_wallpaper(monitor_device_path, path)
        except OSError as e:
            logger.warning(
                "cannot set wallpaper on %s: display may be disconnected", monitor_device_path
            )
            logger.exception(e)

    def update_display_scene(
        self,
        display_id: DisplayId,
        resource: BaseResource,
        resolution: tuple[int, int] | None,
        scale: int | None,
    ) -> None:
        """Record settings on its display and buffer the resource image.

        This is the **update_canvas** stage: resolution/scale are
        recorded in ``state.pending``, and the resource is mounted so its
        ``update_canvas`` buffers the latest image for the next composite.
        Nothing is applied to the display yet — resolution/scale changes
        take effect at :meth:`apply_display_scene` time.

        Args:
            display_id: Opaque id of the target display.
            resource: Resource to mount on the display.
            resolution: Target resolution, or ``None`` to keep the original.
            scale: Target scale percentage, or ``None`` to keep the original.
        """
        with self._lock:
            self._displays[display_id].pending = (resolution, scale)
        self.update_resource(display_id, resource)

    def apply_display_scene(self) -> None:
        """Apply recorded resolution/scale and composite the canvas on every display."""
        with self._lock:
            pending = [d for d, s in self._displays.items() if s.pending is not None]
        for display_id in pending:
            with self._lock:
                recorded = self._displays[display_id].pending
            if recorded is None:
                continue
            resolution, scale = recorded
            if resolution is not None:
                self.set_display_resolution(display_id, resolution[0], resolution[1])
            else:
                with self._lock:
                    self._restore_resolution(self._displays[display_id])
            if scale is not None:
                self.set_display_scale(display_id, scale)
            else:
                with self._lock:
                    self._restore_scale(self._displays[display_id])
            with self._lock:
                self._displays[display_id].pending = None
        self.plot_canvas()

    def update_resource(self, display_id: DisplayId, resource: BaseResource) -> None:
        """Replace the active resource on a display with a new one.

        Demounts the previous resource, binds the new one, and mounts it.

        Args:
            display_id: Opaque id of the display.
            resource: The new resource to activate.
        """
        with self._lock:
            state = self._displays[display_id]
            prev = state.resource
            state.resource = resource
            state.is_patch = False
        prev.demount()  # outside lock (may join a cycling thread)
        resource.mount()  # outside lock (→ update_canvas takes the lock)

    def set_display_resolution(self, display_id: DisplayId, width: int, height: int) -> bool:
        """Set a display's resolution, snapping to a supported mode.

        The requested ``(width, height)`` is snapped to the nearest supported
        resolution from the display's cached capability; a warning is logged
        when the resolved value differs from the request.  The change is
        persisted to the registry (``CDS_UPDATEREGISTRY``) and the pre-change
        resolution is recorded so it can be restored.

        Args:
            display_id: Opaque id of the display.
            width: Requested pixel width.
            height: Requested pixel height.

        Returns:
            True on success, False (logged) when the display is unknown, its
            metadata is unavailable, or the system call failed.
        """
        with self._lock:
            state = self._displays[display_id]
            resolved = resolve_target_resolution((width, height), list(state.capability.resolution))
            if resolved != (width, height):
                logger.warning(
                    "resolution %dx%d not supported on %s; snapping to %dx%d",
                    width,
                    height,
                    state.display_info.monitor_device_path,
                    resolved[0],
                    resolved[1],
                )
            if state.current_resolution == resolved:
                return True
            ok = set_display_resolution(state.display_info.device_name, resolved[0], resolved[1])
            if ok:
                state.current_resolution = resolved
                logger.info(f"display manager set resolution to {resolved[0]}*{resolved[1]}")
            return ok

    def set_display_scale(self, display_id: DisplayId, scale: int) -> bool:
        """Set a display's DPI scale, snapping to a supported step.

        The requested percentage is snapped to the nearest supported scale from
        the display's cached capability (a warning is logged when they differ),
        then applied as a relative step from the reference scale.  The change
        is session-only and the pre-change scale is recorded for restore.

        Args:
            display_id: Opaque id of the display.
            scale: Requested scale percentage (e.g. 150).

        Returns:
            True on success, False (logged) when the display is unknown, has no
            cached capability, or the system call failed.
        """
        with self._lock:
            state = self._displays[display_id]
            scale_rel = resolve_target_scale_step(
                state.capability.reference_scale, scale, state.capability.scale
            )
            reference_index = state.capability.scale.index(state.capability.reference_scale)
            resolved = state.capability.scale[reference_index + scale_rel]
            if resolved != scale:
                logger.warning(
                    "scale %d%% not supported on %s; snapping to %d%%",
                    scale,
                    state.display_info.monitor_device_path,
                    resolved,
                )
            if state.current_scale == resolved:
                return True
            ok = set_display_scale(
                state.display_info.device_name,
                state.display_info.adapter_id,
                state.display_info.source_id,
                scale_rel,
            )
            if ok:
                state.current_scale = resolved
                logger.info(f"display manager set scale to {resolved}")
            return ok

    def _restore_resolution(self, state: DisplayState) -> None:
        """Revert a display's resolution to its original value.

        Idempotent: on success ``current_resolution`` is cleared so a later pass
        is a no-op; on a transient failure it is kept so a later pass retries.
        Must be called with ``self._lock`` held.  No-op for displays the app
        never modified.

        Args:
            state: Display whose resolution to restore.
        """
        if state.current_resolution is None:
            return
        ok = set_display_resolution(
            state.display_info.device_name,
            state.original_resolution[0],
            state.original_resolution[1],
        )
        if ok:
            state.current_resolution = None
            logger.info(
                f"display manager restore resolution to "
                f"{state.original_resolution[0]}*{state.original_resolution[1]}"
            )
        else:
            logger.warning(
                "cannot restore resolution on %s: display may be disconnected",
                state.display_info.monitor_device_path,
            )

    def _restore_scale(self, state: DisplayState) -> None:
        """Revert a display's DPI scale to its original value.

        Idempotent: on success ``current_scale`` is cleared so a later pass is a
        no-op; on a transient failure it is kept so a later pass retries.
        Must be called with ``self._lock`` held.  No-op for displays the app
        never modified.

        Args:
            state: Display whose scale to restore.
        """
        if state.current_scale is None:
            return
        scale_rel = resolve_target_scale_step(
            state.capability.reference_scale,
            state.original_scale,
            state.capability.scale,
        )
        ok = set_display_scale(
            state.display_info.device_name,
            state.display_info.adapter_id,
            state.display_info.source_id,
            scale_rel,
        )
        if ok:
            state.current_scale = None
            logger.info(f"display manager restore scale to {state.original_scale}")
        else:
            logger.warning(
                "cannot restore scale on %s: display may be disconnected",
                state.display_info.monitor_device_path,
            )

    def _resolve_cached_image(
        self,
        img: Path | Image.Image,
        w: int,
        h: int,
    ) -> Image.Image:
        """Resolve a buffer entry through the cache, or fall back to ``Image.open``.

        If *img* is a ``Path`` and the cache is available, ``render()`` handles
        hit-or-miss transparently.  If *img* is a ``Path`` but there is no cache,
        open it directly.  Otherwise return the PIL ``Image`` as-is.

        Args:
            img: Image path or decoded PIL image.
            w: Target width for the cached resize.
            h: Target height for the cached resize.

        Returns:
            The rendered PIL image.
        """
        if isinstance(img, Path) and self._image_cache is not None:
            return self._image_cache.render(img, w, h)
        if isinstance(img, Path):
            return Image.open(img)
        return img

    def update_canvas(
        self,
        display_id: DisplayId,
        style: WallpaperStyle,
        image: Path | Image.Image,
    ) -> None:
        """Buffer a per-monitor wallpaper entry for the next composite.

        Registered once on :class:`BaseResource` as the class-wide buffer
        callback (see :meth:`BaseResource.register_update_canvas`).

        Args:
            display_id: Opaque id of the display.
            style: Wallpaper fit/style for this monitor.
            image: Image path or PIL Image to display.
        """
        with self._lock:
            state = self._displays[display_id]
            state.canvas = (style, image)
            d_info = self._displays[display_id].display_info
            model = d_info.model or "UNKNOWN MODEL"
            device_name = d_info.device_name or "UNKNOWN DEVICE NAME"
            label = f"{model} ({device_name})"
            logger.info(f"canvas update on {label:<25} - {image} [{style.name}]")

    def plot_canvas(self) -> None:
        """Composite buffered per-monitor images into a spanned wallpaper and apply it.

        Reads the buffered per-monitor entries from ``self._displays``
        (``{monitor_id: (style, image)}``), renders each image into its
        monitor's region on the virtual desktop according to its style, then
        applies the composite as a spanned wallpaper via the COM
        ``IDesktopWallpaper`` API.

        Serialized by ``self._lock``: the composite reads the buffer snapshot
        and writes a fresh composite file under the same lock so concurrent
        ``update_canvas`` writes (e.g. from a ``ResourceCycle`` cycling thread)
        cannot corrupt the file.
        """
        with self._lock:
            self._composite_and_apply()

    def _next_composite_path(self, cache_dir: Path) -> Path:
        """Return the next free ``_composite_<time>.png`` under ``cache_dir/composite``.

        Removes stale composites first (best-effort) so the app never truncates
        a file the OS may still be reading.  The time-based suffix comes from
        :meth:`_next_composite_timestamp`, which is serialized by a dedicated
        lock so two plots can never collide on the same filename.

        Args:
            cache_dir: Root cache directory the ``composite`` subdirectory
                lives under.

        Returns:
            A path to an unused composite filename.
        """
        composite_dir = cache_dir / "composite"
        composite_dir.mkdir(parents=True, exist_ok=True)
        self._gc_old_composites(composite_dir)
        while True:
            path = composite_dir / f"_composite_{self._next_composite_timestamp()}.png"
            if not path.exists():
                return path

    def _next_composite_timestamp(self) -> int:
        """Return a monotonic, collision-free timestamp for a composite filename.

        Serialized by ``_composite_lock``: the last-issued value is tracked so a
        call landing on the same instant as the previous one is bumped upward.
        ``time.time_ns()`` also makes names unique across process runs.

        Returns:
            A nanosecond timestamp unique within this process run.
        """
        with self._composite_lock:
            now = time.time_ns()
            if now <= self._composite_last_time:
                now = self._composite_last_time + 1
            self._composite_last_time = now
            return now

    @staticmethod
    def _gc_old_composites(composite_dir: Path) -> None:
        """Best-effort removal of previously-written composite files.

        A file the OS is still reading (the currently-applied wallpaper) cannot
        be deleted and is left for the next pass; a failure here is never fatal.

        Args:
            composite_dir: Directory containing ``_composite_*.png`` files.
        """
        for old in composite_dir.glob("_composite_*.png"):
            try:
                old.unlink()
                logger.debug("removed old composite %s", old)
            except OSError as exc:
                logger.warning("cannot remove old composite %s: %s", old, exc)

    def _composite_and_apply(self) -> None:
        """Composite the buffered images and apply the result as wallpaper.

        Must be called with ``self._lock`` held.
        """
        buffer: dict[DisplayId, tuple[WallpaperStyle, Path | Image.Image]] = {
            s.display_id: s.canvas for s in self._displays.values() if s.canvas is not None
        }
        if not buffer:
            return

        displays = get_display_info()
        if displays is None:
            return

        canvas = self._build_canvas(buffer, displays)
        if canvas is None:
            return

        # Save to a fresh composite file under cache_dir/composite. Each plot
        # uses a new filename so we never truncate a file the OS (DWM/Explorer)
        # may still be reading after the previous set_wallpaper; older files are
        # cleaned up by _gc_old_composites().
        cache_dir = ConfigStore.instance.cache_path
        temp_path = self._next_composite_path(cache_dir)
        canvas.save(temp_path, "PNG")
        logger.debug("canvas composite saved on %s", temp_path)

        # Apply as spanned wallpaper. Mark the app as having claimed the desktop
        # BEFORE applying: even a partial apply leaves the wallpaper SPAN, so any
        # display captured afterwards has no genuine original to record.
        self._wallpaper_applied = True
        with com_session():
            set_wallpaper_style(WallpaperStyle.SPAN)
            set_wallpaper(None, temp_path)
        logger.info("canvas composite applied")

    def _build_canvas(
        self,
        buffer: dict[DisplayId, tuple[WallpaperStyle, Path | Image.Image]],
        displays: list[DisplayInfo],
    ) -> Image.Image | None:
        """Render all buffered per-monitor images into one composite canvas.

        The canvas spans the union bounding rect of the buffered displays that
        are still connected.  Returns ``None`` when none of the buffered
        displays is present in *displays* (e.g. unplugged since the snapshot).

        Args:
            buffer: Per-monitor buffered ``(style, image)`` entries.
            displays: Current display topology snapshot.

        Returns:
            The composite canvas, or None when no buffered display is present
            in *displays*.
        """
        relevant = [d for d in displays if d.display_id in buffer]
        if not relevant:
            return None

        min_left = min(d.position[0] for d in relevant)
        min_top = min(d.position[1] for d in relevant)
        max_right = max(d.position[0] + d.source_resolution[0] for d in relevant)
        max_bottom = max(d.position[1] + d.source_resolution[1] for d in relevant)
        canvas_w = max_right - min_left
        canvas_h = max_bottom - min_top
        logger.debug("canvas composite plot (%d*%d)", canvas_w, canvas_h)

        canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
        display_map = {d.display_id: d for d in relevant}

        # A SPAN resource replaces the entire composite.
        span_entry = next(
            ((mid, path) for mid, (style, path) in buffer.items() if style == WallpaperStyle.SPAN),
            None,
        )
        if span_entry is not None:
            _, img = span_entry
            img = self._resolve_cached_image(img, canvas_w, canvas_h)
            rendered, _, _ = DisplayManager._render_image_for_region(
                img,
                WallpaperStyle.FILL,
                canvas_w,
                canvas_h,
            )
            canvas.paste(rendered, (0, 0))
        else:
            for display_id, (style, img) in buffer.items():
                display = display_map.get(display_id)
                if display is None:
                    continue  # buffered display no longer connected; skip
                self._fill_canvas(canvas, display, style, img, min_left, min_top)

        return canvas

    def _fill_canvas(
        self,
        canvas: Image.Image,
        display: DisplayInfo,
        style: WallpaperStyle,
        img: Path | Image.Image,
        min_left: int,
        min_top: int,
    ) -> None:
        """Render *img* into *display*'s region on *canvas* and paste it there.

        Args:
            canvas: Composite canvas to paste into.
            display: Display defining the target region.
            style: Wallpaper fit/style for the image.
            img: Image path or decoded PIL image.
            min_left: Leftmost canvas origin among all relevant displays.
            min_top: Topmost canvas origin among all relevant displays.
        """
        region_w, region_h = display.source_resolution
        canvas_x = display.position[0] - min_left
        canvas_y = display.position[1] - min_top

        # Keep the source path (if any) for the debug log; the branches below
        # replace *img* with a decoded/cached PIL Image.
        source = img if isinstance(img, Path) else None

        # CENTER/TILE keep native pixels — bypass the cover-resize cache.
        if style in (WallpaperStyle.CENTER, WallpaperStyle.TILE):
            if isinstance(img, Path):
                img = Image.open(img)
        else:
            img = self._resolve_cached_image(img, region_w, region_h)
        rendered, offset_x, offset_y = DisplayManager._render_image_for_region(
            img,
            style,
            region_w,
            region_h,
        )
        canvas.paste(rendered, (canvas_x + offset_x, canvas_y + offset_y))
        label = (
            f"{display.model or 'UNKNOWN MODEL'} "
            f"({display.device_name or 'UNKNOWN DEVICE NAME'} {region_w}*{region_h})"
        )
        logger.debug(
            "canvas composite fill %-30s - %s [%s]",
            label,
            source
            if source
            else f"image (id: {id(img)}  size: {'*'.join(str(i) for i in img.size)}",
            style.name,
        )

    @staticmethod
    def _render_image_for_region(
        img: Image.Image,
        style: WallpaperStyle,
        region_w: int,
        region_h: int,
    ) -> tuple[Image.Image, int, int]:
        """Render *img* into a ``(region_w × region_h)`` rect using *style*.

        Returns ``(rendered_image, offset_x, offset_y)`` so the caller can
        paste at ``(canvas_x + offset_x, canvas_y + offset_y)``.  Only the
        actual image pixels are returned — styles that do not fill the full
        region (CENTER, FIT) leave the surrounding area clean.

        SPAN falls back to FILL (per-monitor spanning is not meaningful).
        Alpha-channel images are composited over a black background first.

        Args:
            img: Source image to render.
            style: Wallpaper fit/style to apply.
            region_w: Region width.
            region_h: Region height.

        Returns:
            The rendered image plus the paste offset ``(offset_x, offset_y)``.
        """
        src_w, src_h = img.size

        # Strip alpha against black background.
        if img.mode == "RGBA":
            bg = Image.new("RGB", img.size, (0, 0, 0))
            bg.paste(img, mask=img.split()[3])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")

        if style == WallpaperStyle.STRETCH:
            return _resize_image(img, region_w, region_h), 0, 0

        if style == WallpaperStyle.FILL:
            scale = max(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = _resize_image(img, new_w, new_h)
            x = (new_w - region_w) // 2
            y = (new_h - region_h) // 2
            return resized.crop((x, y, x + region_w, y + region_h)), 0, 0

        if style == WallpaperStyle.FIT:
            scale = min(region_w / src_w, region_h / src_h)
            new_w = int(src_w * scale)
            new_h = int(src_h * scale)
            resized = _resize_image(img, new_w, new_h)
            offset_x = (region_w - new_w) // 2
            offset_y = (region_h - new_h) // 2
            return resized, offset_x, offset_y

        if style == WallpaperStyle.CENTER:
            offset_x = (region_w - src_w) // 2
            offset_y = (region_h - src_h) // 2
            return img, offset_x, offset_y

        if style == WallpaperStyle.TILE:
            canvas = Image.new("RGB", (region_w, region_h), (0, 0, 0))
            for y in range(0, region_h, src_h):
                for x in range(0, region_w, src_w):
                    canvas.paste(img, (x, y))
            return canvas, 0, 0

        # SPAN or unknown — fall back to FILL.
        return DisplayManager._render_image_for_region(img, WallpaperStyle.FILL, region_w, region_h)
