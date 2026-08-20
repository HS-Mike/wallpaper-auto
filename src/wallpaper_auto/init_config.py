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
# This file defines wallpapers, triggers, rules, and scenes for automatic
# wallpaper switching.  Rules are evaluated in order; the first matching
# rule's target is applied.  If no rule matches, the fallback is used.
#
# Top-level sections:
#   resource         named resources (static, cycle, or custom)
#   trigger          events that trigger re-evaluation
#   rule             ordered condition → target mappings, first match wins
#   scene            per-display bindings, auto-registered as targets
#   fallback_target  resource applied when no rule matches
#   at_shutdown      optional target applied on shutdown/logoff
#   cache            cache dir and resized-image tuning; if omitted, uses
#                    %LOCALAPPDATA%/wallpaper-auto/cache
#   logging          optional log level and log file
# =============================================================================


# ---------------------------------------------------------------------------
# Resources  (resource pool)
# ---------------------------------------------------------------------------
# Each key is a name you can reference in rules.
# Two forms are accepted:
#
#   Full form  — dict with ``name`` (component type) and ``config``:
#   shortcut: { name: static_wallpaper, config: { path: "...", style: fill } }
#
#   Shorthand  — a plain string treated as the image path; coerced into a
#   ``static_wallpaper`` resource with ``style: fill``:
#   shortcut: "C:/path/to/image.jpg"
# ---------------------------------------------------------------------------
resource:

  # Full-form resource — explicit component name + config
  office_view:
    name: static_wallpaper
    config:
      path: "C:/Users/You/Pictures/office.jpg"
      style: fill               # fill | fit | stretch | center | tile

  # Shorthand — bare path string; coerced to a ``static_wallpaper`` resource
  # with ``style: fill``
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
# Each entry uses the general component format — a ``name`` (the component
# type) plus an optional ``config`` dict of parameters.
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
# Each entry assigns a resource to monitors whose model matches. Bindings are
# checked in order; the first that matches a monitor wins. Two match styles:
#   - ``display_model``        — exact, case-sensitive model name
#   - ``match_display_model``  — regular expression pattern (re.search)
# Exactly one of the two must be given per binding.
#
# Optional per-binding display controls:
#   - ``resolution``  — target display resolution, e.g. "1920x1080" or
#                       [1920, 1080]; snapped to a supported mode
#   - ``scale``       — target display scale, e.g. 1.5 (= 150%) or 150
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
#     - match_display_model: "27.*"      # regex: any 27-inch monitor
#       resource: "office_view"
#       resolution: "1920x1080"
#       scale: 1.5
#   mobile:
#     - match_display_model: ".*"        # regex catch-all
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
#   process_running: <basename_or_path>    # e.g. "notepad.exe" or "C:/Windows/..."
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

  # ── Example 6: Process-running rule ──────────────────────────────────────
  # Switches to a dark wallpaper while a game is running. ``process_running``
  # accepts a basename (any location) or a full path (exact match).
  # - name: "game_running"
  #   condition:
  #     process_running: "game.exe"
  #   target: "black"

  # ── Example 7: Rule targeting a scene ────────────────────────────────────
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
# and the resized per-display images.  If ``path`` is omitted, it defaults
# to %LOCALAPPDATA%/wallpaper-auto/cache.  ``resize`` tunes the resized-image
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


# ---------------------------------------------------------------------------
# Logging  (optional)
# ---------------------------------------------------------------------------
# Controls log verbosity and destination.
#   - level:  DEBUG | INFO | WARNING | ERROR (default DEBUG)
#   - file:   optional log file path; console-only logging if omitted
# The ``-l``/``--log-level`` and ``--log-file`` flags and
# ``run()``/``run_service()`` arguments take precedence over these values.
# ---------------------------------------------------------------------------
# logging:
#   level: INFO
#   file: "C:/Users/You/wallpaper-auto.log"
"""


def generate_template(output_path: str, force: bool = False) -> None:
    """Write the starter config template to *output_path*.

    Args:
        output_path: Filesystem path for the generated config file.
        force: If ``False`` (the default) and *output_path* already exists,
            raise :class:`FileExistsError`.

    Raises:
        FileExistsError: If *output_path* exists and *force* is ``False``.
    """
    resolved = os.path.realpath(output_path)

    if os.path.exists(resolved) and not force:
        raise FileExistsError(f"{resolved} already exists. Use -f/--force to overwrite.")

    os.makedirs(os.path.dirname(resolved), exist_ok=True)

    with open(resolved, "w", encoding="utf-8") as f:
        f.write(_TEMPLATE)

    print(f"Created starter config at {resolved}")
