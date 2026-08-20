# Wallpaper Auto

[![codecov](https://codecov.io/github/HS-Mike/wallpaper-auto/graph/badge.svg?token=BZSTAYXUWF)](https://codecov.io/github/HS-Mike/wallpaper-auto)

Rule-driven wallpaper switching for Windows with per-display resolution and scale setting.
## How It Works

The application has the following key concepts:

| Component | Role |
|-----------|------|
| **Resource** | Applies a wallpaper to the desktop — either a single static image or a rotating slideshow of multiple images; a **Resource** is unaware of actual displays |
| **Scene** | Like a **Resource**, but it specifies display match info and resolution and scale settings |
| **Trigger** | Watches for changes and notifies the app when one occurs |
| **Rule** | A set of conditions paired with a target; the target (either a **Resource** or a **Scene**) is applied when the conditions match |
| **Evaluator** | Checks a single condition and returns true/false. Every condition in a **Rule** maps to a corresponding evaluator |


A Trigger detects a change (e.g., you connect to "OfficeWiFi") and notifies the controller. The controller runs the **Rule Engine**, which evaluates each rule's conditions using **Evaluators**. The first rule whose conditions all match determines the target (a **Resource** or a **Scene** ) to apply. 
target will be resolved to the actual wallpaper, resolution, and scale for each display at runtime, then those settings are applied via the Windows API.

```
Trigger fires
    |
    v
Rules are evaluated top-to-bottom, and a target is selected.
    |
    v
Target is resolved to proposed settings for each display 
(per-monitor resource + resolution + scale)
    |
    v
Each display prepares its wallpaper at those settings
```

## Quick Start


1. Install the package:

   ```bash
   pip install wallpaper-auto
   ```

2. Create `config.yaml` in the current directory:

   ```yaml
   resource:
     office_view: "C:/Users/You/Pictures/wallpaper1.jpg"
     home_view: "C:/Users/You/Pictures/wallpaper2.jpg"

   trigger:
     - name: time
       config:
         interval: 60          # Re-evaluate rules every minute

   rule:
     - name: "work_hours_at_office"
       condition:
         day_of_week_is: [0, 1, 2, 3, 4]     # Monday to Friday
       target: "office_view"

   fallback_target: "home_view"              # Applied when no rule matches
   ```

   This minimal config shows a simple weekday/weekend split:

   - **Weekdays (Mon–Fri)** — applies `office_view`.
   - **All other times** (evenings and weekends) — applies `home_view`, since the fallback is used whenever no rule matches.

   A `time` trigger re-evaluates the rules every minute (though too frequently in practice), so the wallpaper switches as soon as the day changes.

3. Run:

   ```bash
   wallpaper-auto run -c config.yaml
   ```


## How Wallpapers, Resolution, and Scale Are Applied

The Windows COM `IDesktopWallpaper` API only allows a uniform style across all displays. To support per-display wallpaper style settings, the app applies wallpapers as a single SPAN wallpaper rather than one per monitor. Each display's prepared image is laid out into one canvas (each with its proposed style) that stretches across the entire virtual desktop, so Windows sees a single wallpaper covering all monitors.

The app also applies **resolution** and **scale** alongside the wallpaper. You cannot assign arbitrary resolution or scale values to a display — they must fall within the display's supported capability options.

Each display's original wallpaper, resolution, and DPI scale are captured when the display is added, and its supported resolution/scale modes are cached for snapping on apply. On stop or display removal, every display is reverted to its recorded originals (wallpaper, resolution, and scale). Note: if a new display is plugged in during app runtime, Windows cannot capture its original wallpaper setting because the app's single SPAN wallpaper overrides it — a black background is set as the fallback in that case.


## Configuration

Generate a starter config with all options documented in the current directory:

```bash
wallpaper-auto init-config
```

A config file has following sections:

   | Section | Required | Remark |
   |----------------|----------|--------|
   | `resource` | required | |
   | `scene` | optional | |
   | `trigger` | required | |
   | `rule` | required | |
   | `fallback_target` | required | Target applied when no rule matches |
   | `at_shutdown` | optional | Target applied on Windows shutdown or logoff |
   | `cache` | optional | Cache dir and resized-image tuning |
   | `logging` | optional | Log level and optional log file |

   *The app parses config file with Pydantic using the `ConfigModel` class in `wallpaper_auto/models.py`*.


---


### Section - `resource`

*Refer to ResourceConfig in wallpaper_auto.models*

Generally, `resource` specifies the asset the app tries to apply on an actual display. Configurations in this section should be stated as a dict, whose key will serve as the id. Other components can use this id to refer to the corresponding resource.

__static_wallpaper__

A simple image used as wallpaper.

 ```yaml
resource:
  
  black: "C:/Users/You/Pictures/black.jpg"    # Shorthand  with ``style: fill``

  office_view:
    name: static_wallpaper
    config:
      path: "C:/Users/You/Pictures/office.jpg"
      style: fill                            # fill | fit | stretch | center | tile
```

__cycle__

Serve as Slideshow show function. Resource that cycles through a list of sub-resources. This is an wrapper on several resources. 

```yaml
resource:
  slideshow_view:                      
    name: cycle
    config:
      resources:                          
        # Sub-resources to cycle through
        # each item in this list is a complete resource config
        - name: static_wallpaper
          config: {path: "C:/Pictures/picture1.jpg", style: fill}
        - name: static_wallpaper
          config: {path: "C:/Pictures/picture2.jpg", style: fit}
        - "C:/Pictures/picture3.jpg"
      interval: 300                       # Seconds between switches 
      random: false                       # true = random order, false = sequential
```
     

---

### Section - `scene`

*Refer to SceneBinding in wallpaper_auto.models*

Scenes assign resources to specific monitors so each display can show its own wallpaper in a multi-monitor setup. Configurations in this section should be stated as a dict, whose key will serve as the id. Other components can use this id to refer to the corresponding scene. Each value is a list of bindings mapping a display model pattern to a resource, with optional per-display `resolution` and `scale`. Scene names are auto-registered as rule targets.

__SceneBinding__

Each binding maps a display model pattern to a resource, with optional per-display resolution and scale.

```yaml
scene:
  - layout_1:
      display_model: "U2719D"          # exact match
      resource: "work_wallpaper"
      resolution: "1920x1080"          # "1920X1080", "1920*1080", "1920, 1080", [1920, 1080] are all acceptable format
      scale: 150                       # float representation (1.75) will be translated to percentage int (175)

  - layout_2:
      match_display_model: "U27.+"     # regex, matched via re.search
      resource: "secondary_wallpaper"
```

---

### Section - `trigger`

*Refer to TriggerConfig in wallpaper_auto.models*

A list of triggers that watch for changes and notify the app to re-evaluate the rules. Each item pairs a `name` (the trigger type) with a `config` block whose keys are unpacked as constructor arguments.

__time__

Fires callbacks at fixed daily times and/or on a periodic interval.

```yaml
trigger:
  - name: time
    config:
      interval: 60          # Re-evaluate every 60 seconds
      times:                # Fixed daily trigger times
        - "09:00"
        - "18:00"
```

__network__

Fires when the WiFi / network changes.

```yaml
trigger:
  - name: network
    # no config required
```

__windows_session__

Fires on lock / unlock / logon / logoff.

```yaml
trigger:
  - name: windows_session
    # no config required
```

__display__

Fires on monitor plug / unplug and DPI scale change.

```yaml
trigger:
  - name: display
    # no config required
```

__process__

Fires on process start / stop events for a configurable list of executable identifiers.

Each entry is classified by whether it contains a directory component:

- a bare filename like `"notepad.exe"` — matches any process with that basename
- a full path like `"C:\Windows\System32\notepad.exe"` — matches only processes launched from that exact path


```yaml
trigger:
  - name: process
    config:
      exe_names:                    # Required: list of executable identifiers to watch
        - "notepad.exe"             # matches any notepad.exe
        - "C:\\Windows\\System32\\mspaint.exe"   # matches only this exact path
```


---

### Section - `rule`

*Refer to Rule and ConditionNode in wallpaper_auto.models*

Ordered rules, evaluated top-to-bottom; the first match wins. Each rule has a `name`, a `target`, and a `condition`. The `target` should be a `resource` / `scene` ID.

__wifi_ssid_is__

True when connected to the given WiFi SSID.

```yaml
rule:
  - name: "At work"
    target: "work_wallpaper"
    condition:
      wifi_ssid_is: "OfficeWiFi"
```

__day_of_week_is__

True when today's weekday is in the list (`0` = Monday ... `6` = Sunday).

```yaml
rule:
  - name: "Work days"
    target: "office_wallpaper"
    condition:
      day_of_week_is: [0, 1, 2, 3, 4]    # Monday to Friday
```

__in_time_range__

True when the current time falls within the `"HH:MM"` range (supports overnight ranges).

```yaml
rule:
  - name: "intraday"
    condition:
      in_time_range: ["13:00", "14:00"]
    target: "office_view"

  - name: "overnight"
    condition:
      in_time_range: ["23:00", "06:00"]
    target: "home view"
```

__have_display__

True when a connected display's model name matches the regex (`re.search`).

```yaml
rule:
  - name: "External monitor"
    condition:
      have_display: "U2719D"
    target: "work_layout"
```

__process_running__

True when a process matching the identifier is currently running. A bare filename like `"notepad.exe"` matches any process with that basename; a full path like `"C:\Windows\System32\notepad.exe"` matches only processes launched from that exact path (case-insensitive).

```yaml
rule:
  - name: "Game running"
    condition:
      process_running: "game.exe"
    target: "dark_theme"

  - name: "Specific app instance"
    condition:
      process_running: "C:\\Program Files\\MyApp\\app.exe"
    target: "work_wallpaper"
```

__and / or__

Combine nested conditions; all (`and`) or any (`or`) child must match.

```yaml
rule:
  - name: "Work hours at office"
    target: "work_wallpaper"
    condition:
      and:
        - day_of_week_is: [0, 1, 2, 3, 4]
        - in_time_range: ["09:00", "18:00"]
```

---

### Section - `fallback_target`

*Refer to ConfigModel in wallpaper_auto.models*

The `resource` / `scene` ID applied when no rule matches. Must reference an existing resource.

```yaml
fallback_target: "default_wallpaper"
```

---

### Section - `at_shutdown`

*Refer to ConfigModel in wallpaper_auto.models*

The resource ID applied when Windows shuts down or the user logs off. Must reference an existing resource.

```yaml
at_shutdown: "work_wallpaper"
```

---

### Section - `cache`

*Refer to CacheConfig in wallpaper_auto.models*

Cache directory plus resized-image cache tuning. `path` is the shared cache dir used by both the composited wallpaper and the resized per-display images; `resize` configures the resized-image cache.

__CacheResizeConfig__

Tuning knobs for the resized-image cache.

```yaml
cache:
  path: "C:/Users/You/.cache/wallpaper_auto"
  resize:
    enabled: true           # Enable the resized-image cache (default true)
    max_size_mb: 200        # Max total size of the resized cache in MB (default 200)
    evict_ratio: 0.9        # Evict down to this fraction of max (default 0.9)
```

---

### Section - `logging`

*Refer to LoggingConfig in wallpaper_auto.models*

Log level and optional log file. Logging resolves by precedence: `run()` arguments (including the `-l`/`--log-file` flags) override this section, which in turn overrides the built-in defaults (`DEBUG`, console-only).

```yaml
logging:
  level: INFO            # DEBUG | INFO | WARNING | ERROR (default DEBUG)
  file: "app.log"        # optional; console-only if omitted
```

## Running

```bash
# Generate a starter config file
wallpaper-auto init-config

# Start the service with the default config.yaml
wallpaper-auto run

# Specify config file
wallpaper-auto run -c /path/to/config.yaml

# Set log level
wallpaper-auto run -l INFO

# Log to a file (console-only by default)
wallpaper-auto run --log-file /path/to/log.txt
```

The `run` command enforces a single instance: if another `wallpaper-auto`
process is already running, the new process logs the conflict and exits
with code 1.

Or start programmatically from Python:

```python
from wallpaper_auto import run
run("config.yaml")
```

## Auto Start

To launch automatically at logon, create a **Task Scheduler** task with an **At log on** trigger. Use `pythonw.exe` to hide the console window:

```bash
pythonw.exe -m wallpaper_auto run -c config.yaml
```

## System Tray

After running, the app displays an icon in the system tray:

- **AUTO**: Switches to automatic rule-driven wallpaper selection. The active target (the resource or scene the rule engine most recently matched, or the fallback) is marked on the menu.
- **Wallpaper targets**: One menu item per resource/scene. Clicking a target switches to MANUAL mode and applies that target immediately. In MANUAL mode, the selected target stays active until AUTO is clicked again.
- **quit**: Stops the service.

## Programmatic Usage

You can start the service from Python code using `run()`.

```python
from wallpaper_auto import run

# Start with the default config.yaml
run("config.yaml")

# With custom components registered inline
run(
    "config.yaml",
    custom_triggers={"my_trigger": MyTrigger},
    custom_resources={"my_resource": MyResource},
    custom_evaluators={"my_evaluator": MyEvaluator()},
)
```

## Custom Components

**Resource**, **Trigger**, and **Evaluator** are extensible components. The recommended way to register custom components is by passing them to `run()` via the `custom_triggers`, `custom_resources`, and `custom_evaluators` keyword arguments. All base classes are importable from the top-level `wallpaper_auto` package.

### Custom Resource

The previous section described **Resource** as the asset the app tries to apply on an actual display. In fact, this component is far more flexible than just applying a simple static wallpaper. 

Extend `BaseResource` and pass it through `run()`.

```python
from wallpaper_auto import BaseResource, run

class CustomResource(BaseResource):
    """a custom resource that does nothing"""
    pass

run("config.yaml", custom_resources={"custom": CustomResource})
```

**BaseResource** has 4 important methods: 2 lifecycle notification methods (``mount()`` and ``demount()``) and 2 wallpaper canvas manage methods (``update_canvas(style, image)`` and ``plot_canvas()``).

`mount()` and `demount()` are lifecycle **notifications** — they signal that the resource has entered or left the active window. Do not execute time-consuming processes in these methods. Otherwise, this will block the application's worker loop. Run the work in a thread instead.

A custom resource never sets the wallpaper directly. It buffers an image for its bound monitor and asks the system to composite it:

- **`update_canvas(style, image)`** — buffer an image for the resource's monitor. `style` is a `WallpaperStyle` (e.g. `fill`, `fit`); `image` is a `Path` or a PIL `Image`. Buffering alone does **not** apply the wallpaper.
- **`plot_canvas()`** — request a composite of the buffered canvas. In the running app this is enqueued to the controller's worker loop rather than composited synchronously, so it is safe to call from any thread.

In fact, you are free to call `update_canvas()` and `plot_canvas()` in between the lifecycle. In ``ResourceCycle``, there is a dedicated thread that starts in `mount()` and joins in `demount()` to manage the wallpaper rotation dynamically. Under a trusted environment, you can even execute bash scripts to get richer customized features. 

At runtime, a resource can inspect the display it is bound to through `self.display` (a `DisplayInfo` set before `mount()`). This is an **instance attribute**: the manager sets it on each instance when it binds that instance to a monitor (before `mount()`), so every instance tracks its own bound display — unlike the class-wide `update_canvas()` / `plot_canvas()` callbacks, which are uniform across all instances.

**WARNING:** Resource instances are **not reused**. Each time a target is applied, the manager creates a fresh instance per connected display, binds it to a single monitor (accessible via `self.display`), and calls `mount()` once and `demount()` once. Expect a **different instance across each wallpaper apply session and per display**.

The keys under `config` are unpacked as keyword arguments to the resource's `__init__` when the manager constructs the instance. 

The example below shows a custom resource that applies a wallpaper from a time-consuming download. It integrates all the `BaseResource` features described above: `__init__` accepts the config keys, a worker thread started in `mount()` runs the time-consuming download using the bound display's resolution, and `update_canvas` / `plot_canvas` apply the result. `demount()` joins the thread. The manager unpacks `config:` keys into `__init__` kwargs — so `query: "mountain"` and `style: fill` become `OnlineResource(query="mountain", style="fill")`. Define `__init__` parameters to match the config keys you expect — a default value makes each key optional.


```python
import threading
from wallpaper_auto import BaseResource, run

class OnlineResource(BaseResource):
    """Custom resource that downloads a wallpaper on a worker thread."""

    def __init__(self, query: str = "nature", style: str = "fill"):
        # `query` and `style` come from the `config:` block below
        super().__init__()
        self.query = query
        self.style = style
        self._worker_thread: threading.Thread | None = None

    def mount(self) -> None:
        # `self.display` is the DisplayInfo bound to this instance (set by the
        # manager before mount). Pass its source resolution to the download fn.
        resolution = self.display.source_resolution  # (width, height) tuple
        self._worker_thread = threading.Thread(
            target=self._download_and_apply,
            args=(resolution,),
            daemon=True,
        )
        self._worker_thread.start()

    def demount(self) -> None:
        if self._worker_thread is not None:
            self._worker_thread.join(timeout=3.0)
            self._worker_thread = None

    def _download_and_apply(self, resolution: tuple[int, int]) -> None:
        # `download_image` is your time-consuming fetch
        image_path = download_image(self.query, resolution)
        self.update_canvas(self.style, image_path)
        self.plot_canvas()

run("config.yaml", custom_resources={"online": OnlineResource})
```

```yaml
resource:
  daily:
    name: online
    config:
      query: "mountain"
      style: fill
```


### Custom Trigger

Custom Trigger must inherit from `BaseTrigger`.

**BaseTrigger** has 3 important methods: 2 lifecycle notification methods (``start()`` and ``stop()``) and 1 evaluation-trigger method (``trigger()``).

Override start() and stop() to manage the trigger's lifecycle; call trigger() between them to request the app to re-evaluate the rules.

For triggers that poll or watch in the background, extend **`BaseThreadTrigger`**. It manages a daemon thread for you: subclass and override `run()` — the framework calls it on the thread and joins it on shutdown. Exit the loop when `self.stop_event.is_set()`, and call `self.trigger()` to fire the callback.

The keys under `config:` are unpacked as keyword arguments to the trigger's `__init__` when the manager constructs the instance.

The example below demonstrates a custom trigger that fires when a USB device is plugged in or unplugged. It inherits from `BaseThreadTrigger`.

```python
from wallpaper_auto import BaseThreadTrigger, run

class UsbPlugTrigger(BaseThreadTrigger):
    """Polls for USB insert/removal and fires the trigger callback."""

    def __init__(self, poll_interval: int = 5):
        # `poll_interval` comes from the `config:` block below
        super().__init__()
        self.poll_interval = poll_interval

    def run(self) -> None:
        while not self.stop_event.is_set():
            # Poll for USB insertion/removal (your detection logic)
            ...
            self.trigger()
            self.stop_event.wait(timeout=self.poll_interval)

run("config.yaml", custom_triggers={"usb_plug": UsbPlugTrigger})
```

```yaml
trigger:
  - name: usb_plug
    config:
      poll_interval: 5
```

### Custom Evaluator

Custom Evaluators allow you to write your own condition checks in `Rule` config.

**BaseEvaluator** is a callable protocol. Subclass and implement `__call__(self, param) -> bool` — the rule engine calls it with the leaf's value from the YAML. Unlike triggers and resources, evaluators receive their input as a single positional argument (whatever shape your YAML leaf has), not via `__init__`, so the param can take any shape you write in YAML. Construct an instance and register it via `custom_evaluators={...}`:

```python
from wallpaper_auto import BaseEvaluator, run

class GeoEvaluator(BaseEvaluator):
    """Evaluate whether the current machine is within `radius` km of (lat, lon)."""

    def __call__(self, param: dict) -> bool:
        # param = {lat: 31.23, lon: 121.47, radius: 0.5}
        # 1. Validate input: param must contain lat, lon, radius
        # 2. Resolve current location via IP geolocation API
        # 3. Compute distance between current location and target
        # 4. Return True if distance <= radius
        ...

run("config.yaml", custom_evaluators={"in_geo_range": GeoEvaluator()})
```

```yaml
rule:
  - name: "Near home"
    condition:
      in_geo_range:
        lat: 31.23
        lon: 121.47
        radius: 0.5
    target: "home_wallpaper"
```

### Registering All Three Together

All custom component types can be registered in a single `run()` call:

```python
from wallpaper_auto import (
    BaseResource,
    BaseThreadTrigger,
    BaseEvaluator,
    run,
)

class MyResource(BaseResource): ...
class MyTrigger(BaseThreadTrigger): ...
class MyEvaluator(BaseEvaluator): ...

run(
    "config.yaml",
    custom_triggers={"my_trigger": MyTrigger},
    custom_resources={"my_resource": MyResource},
    custom_evaluators={"in_geo_range": MyEvaluator()},
)
```

### Alternative: Class-level Registration

As an alternative, you can register components directly on the manager classes before calling `run()`. This is useful when the registration must happen before the configuration is loaded (e.g., in a plugin system).

```python
from wallpaper_auto import ResourceManager, RuleEngine, TriggerManager, run

ResourceManager.register_resource("online", OnlineResource)
TriggerManager.register_trigger("usb_plug", UsbPlugTrigger)
RuleEngine.register_evaluator("my_evaluator", MyEvaluator())

run("config.yaml")
```

## Dependencies

- Python 3.12+
- PyYAML — config file parsing
- Pydantic >=2.0 — config validation & data models
- PySide6 — system tray UI
- pywin32 — Windows session & display event monitoring (lock/unlock/logon/logoff, monitor plug/unplug), shutdown detection, and display info
- wmi — WMI queries for WiFi/network SSID detection
- Pillow — image resizing & caching for wallpaper compositing

## License

MIT
