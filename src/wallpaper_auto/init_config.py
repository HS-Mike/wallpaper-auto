"""
Generate a starter YAML configuration file for the wallpaper auto.

This module is invoked via the ``init-config`` CLI subcommand and writes a
well-commented template to the specified path.
"""

import os

_TEMPLATE = """\
# =============================================================================
# Wallpaper Auto — Configuration Template
# =============================================================================
# This file defines wallpapers, triggers, and rules for automatic wallpaper
# switching.  Rules are evaluated in order; the first matching rule's target
# is applied.  If no rule matches, the fallback is used.
# =============================================================================


# ---------------------------------------------------------------------------
# Resources  (wallpaper pool)
# ---------------------------------------------------------------------------
# Each key is a logical name you can reference in rules.
# Two forms are accepted:
#
#   Full form  — dict with ``name`` (component type) and ``config``:
#   shortcut: { name: static_wallpaper, config: { path: "...", style: fill } }
#
#   Shorthand  — a plain string that is treated as the image path:
#   shortcut: "C:/path/to/image.jpg"
# ---------------------------------------------------------------------------
resource:

  # Full-form resource — explicit component name + config
  office_view:
    name: static_wallpaper
    config:
      path: "C:/Users/You/Pictures/office.jpg"
      style: fill               # fill | fit | stretch | center | tile

  # Shorthand — path only; name and style are inferred
  black: "C:/Users/You/Pictures/black.jpg"

  # ── Resource cycle (cycles through multiple sub-resources) ─────────────
  # cycle:
  #   name: cycle
  #   config:
  #     resources:
  #       - name: static_wallpaper
  #         config: {path: "C:/morning.jpg", style: fill}
  #       - name: static_wallpaper
  #         config: {path: "C:/afternoon.jpg", style: fill}
  #     interval: 300          # seconds between switches (default 300)
  #     random: false          # true = random order, false = sequential


# ---------------------------------------------------------------------------
# Triggers  (what events cause re-evaluation)
# ---------------------------------------------------------------------------
trigger:
  - name: network              # Fires when WiFi SSID changes
  - name: time                 # Fires on a polling interval to check time rules
    config:
      interval: 60             # Re-evaluate rules every 60 seconds
      times:                   # (optional) Fixed daily trigger times
        - "09:00"
        - "18:00"
  - name: windows_session      # Fires on lock / unlock / resume
  - name: display              # Fires on monitor plug / unplug


# ---------------------------------------------------------------------------
# Display Scenes  (per-display wallpaper bindings — auto-registered as
#                  resources)
# ---------------------------------------------------------------------------
# Each entry defines how to assign resources to specific monitors by model.
# ``display_model`` is matched via ``re.search`` against the monitor's model
# name (case-sensitive, partial match allowed); first match wins per monitor.
#
# Display scene entries are **auto-registered** as wallpaper resources.
# A rule's ``target`` can reference a scene name directly — no need to
# add a ``scene`` resource entry in the resource section above.
#
# Both resource IDs and scene names are valid rule ``target`` values.
# Targets are resolved against the ``resource`` section first, then
# ``scene``.
# ---------------------------------------------------------------------------
# scene:
#   work_layout:
#     - display_model: "U2719D"
#       resource: "office_view"
#     - display_model: "internal"
#       resource: "black"
#   mobile:
#     - display_model: ".*"
#       resource: "black"


# ---------------------------------------------------------------------------
# Rules  (condition → target mapping)
# ---------------------------------------------------------------------------
# Rules are checked top-to-bottom.  The **first** rule whose condition
# evaluates to True wins.
#
# A condition is either:
#   - A single evaluator leaf: { <evaluator>: <value> }
#   - An ``and`` / ``or`` combinator: { and: [ ... ] } / { or: [ ... ] }
#
# Available leaf evaluators:
#   wifi_ssid_is: <ssid_string>
#   in_time_range: ["HH:MM", "HH:MM"]
#   day_of_week_is: [0, 1, 2, 3, 4, 5, 6]  # 0=Monday ... 6=Sunday
#   have_display: <model_name_or_regex>    # e.g. "U2719D" or "27.*"
#
# ``target`` can reference a resource ID or a ``scene`` name — the
# resource section is checked first, then scene.
# (Display scene entries are auto-registered as resources.)
# ---------------------------------------------------------------------------
rule:

  # ── Example 1: Simple single-condition rule (targets a resource) ─────────
  - name: "at_office"
    condition:
      wifi_ssid_is: "Company_WiFi"
    target: "office_view"

  # ── Example 2: Compound condition (all must be true) ─────────────────────
  - name: "work_hours_at_office"
    condition:
      and:
        - wifi_ssid_is: "Company_WiFi"
        - day_of_week_is: [0, 1, 2, 3, 4]    # Monday to Friday
        - in_time_range: ["09:00", "18:00"]
    target: "office_view"

  # ── Example 3: Nested combinators (any of the inner groups) ──────────────
  - name: "night_or_weekend_morning"
    condition:
      or:
        - in_time_range: ["22:00", "06:00"]
        - and:
            - day_of_week_is: [5, 6]    # Saturday, Sunday
            - in_time_range: ["06:00", "12:00"]
    target: "black"

  # ── Example 4: Day-of-week rule ──────────────────────────────────────────
  - name: "weekend_vibes"
    condition:
      day_of_week_is: [5, 6]    # Saturday, Sunday
    target: "office_view"

  # ── Example 5: Per-display wallpaper rule (targets a scene name) ────
  # - name: "external_monitor"
  #   condition:
  #     have_display: "U2719D"
  #   target: "work_layout"      # scene name — auto-registered as a resource

  # ── Example 6: Rule targeting a scene ────────────────────────────────────
  # - name: "mobile"
  #   condition:
  #     wifi_ssid_is: "CoffeeShop"
  #   target: "mobile"          # scene name — auto-registered as a resource


# ---------------------------------------------------------------------------
# Fallback  (used when no rule matches)
# ---------------------------------------------------------------------------
fallback_target: "office_view"

# ---------------------------------------------------------------------------
# At-Shutdown Wallpaper  (optional)
# ---------------------------------------------------------------------------
# If set, this resource is applied when Windows shuts down or the user logs
# off.  The resource ID must exist in the resource pool above.
# Uncomment the line below and replace with one of your resource IDs:
# ---------------------------------------------------------------------------
# at_shutdown: "office_view"


# ---------------------------------------------------------------------------
# Cache  (optional)
# ---------------------------------------------------------------------------
# Controls the wallpaper cache directory and the resized-image cache.
# ``path`` is the shared cache dir used for both the composited wallpaper
# and the resized per-display images.  ``resize`` tunes the resized-image
# cache component:
#   - enabled:        set to false to disable the resized-image cache entirely
#                     (default true; when disabled each composite loads and
#                     resizes images directly)
#   - max_size_mb:    max total size of the resized cache in MB (default 200)
#   - evict_ratio:    when over the limit, evict down to this fraction of
#                     the max before stopping (default 0.9)
# All fields are optional.
# ---------------------------------------------------------------------------
# cache:
#   path: "C:/Users/You/.cache/wallpaper_auto"
#   resize:
#     enabled: true
#     max_size_mb: 200
#     evict_ratio: 0.9
"""


def generate_template(output_path: str, force: bool = False) -> None:
    """Write the starter config template to *output_path*.

    Parameters
    ----------
    output_path:
        Filesystem path for the generated config file.
    force:
        If ``False`` (the default) and *output_path* already exists, raise
        :class:`FileExistsError`.

    Raises
    ------
    FileExistsError
        If *output_path* exists and *force* is ``False``.
    """
    resolved = os.path.realpath(output_path)

    if os.path.exists(resolved) and not force:
        raise FileExistsError(f"{resolved} already exists. Use -f/--force to overwrite.")

    os.makedirs(os.path.dirname(resolved), exist_ok=True)

    with open(resolved, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE)

    print(f"Created starter config at {resolved}")
