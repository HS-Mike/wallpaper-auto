# Wallpaper Automator

[![codecov](https://codecov.io/github/HS-Mike/wallpaper_automator/graph/badge.svg?token=BZSTAYXUWF)](https://codecov.io/github/HS-Mike/wallpaper_automator)

Automatically switches Windows desktop wallpapers based on configurable conditions (time, WiFi, day of week).

## Features

- **Multi-condition triggers**: Supports Windows session changes, network changes, and time changes as three types of triggers
- **Flexible rules**: Supports AND/OR condition combinations, can evaluate multiple conditions simultaneously
- **Condition types**:
  - `network`: Current connected WiFi name
  - `day_of_week_is`: Day-of-week check (0=Monday ... 6=Sunday)
  - `time_range`: Time range (supports crossing midnight)
- **System tray control**: System tray menu for manual wallpaper switching and pause/resume auto-switching
- **Thread-safe**: Each monitoring module runs independently without blocking others
- **At-shutdown wallpaper**: Optionally apply a specific wallpaper when Windows shuts down or the user logs off

## Quick Start

1. Download the project to your local machine and enter the directory

2. Install the package:

   ```bash
   pip install .
   ```

3. Generate a starter config file:

   ```bash
   python -m wallpaper_automator init-config
   ```

4. Edit config.yaml with your wallpaper paths and rules, then run:

   ```bash
   python -m wallpaper_automator
   ```

## Configuration

Generate a starter config with all options documented:

```bash
python -m wallpaper_automator init-config
# Creates config.yaml in the current directory
```

Or specify a custom path and force-overwrite an existing file:

```bash
python -m wallpaper_automator init-config myconfig.yaml -f
```

See `python -m wallpaper_automator init-config --help` for all options. You can also create a `config.yaml` file manually (or specify the path via `-c`).

### Configuration Structure

```yaml
# 1. Wallpaper resource pool
resource:
  work_wallpaper:                         # Resource ID — referenced by rules & fallback
    name: static_wallpaper                # Single-image wallpaper
    config:
      path: "C:/path/to/wallpaper.jpg"
      style: fill                         # fill / fit / stretch / center / tile
      restore: false                      # restore original wallpaper on demount (default false)

  carousel:                               # Multi-image cycling wallpaper
    name: dynamic_wallpaper
    config:
      paths:                              # List of images to cycle through
        - "C:/Pictures/morning.jpg"
        - "C:/Pictures/afternoon.jpg"
      style: fill
      interval: 300                       # Seconds between switches (default 300)
      random: false                       # true = random order, false = sequential
      restore: false                      # restore original wallpaper on demount (default false)

# 2. Trigger configuration
trigger:
  - name: network                         # Monitor WiFi / network changes
    config: {}
  - name: time                            # Periodic time-based evaluation
    config: {}
  - name: windows_session                 # Monitor lock/unlock/logon/logoff
    config: {}

# 3. Rules (evaluated top-to-bottom; first match wins)
rule:
  - name: "At work"
    condition:
      wifi_ssid_is: "OfficeWiFi"          # Leaf condition — evaluator name + params
    target: "work_wallpaper"
  - name: "Night mode"
    condition:
      and:                                # AND/OR combinators supported
        - in_time_range: ["23:00", "06:00"]
        - day_of_week_is: [0, 1, 2, 3, 4] # Monday to Friday
    target: "dark_wallpaper"
# 4. Fallback wallpaper (used when no rule matches)
fallback: "default_wallpaper"

# 5. (Optional) At-shutdown wallpaper — applied when Windows shuts down
# at_shutdown: "work_wallpaper"
```

## Running

```bash
# Generate a starter config file
python -m wallpaper_automator init-config

# Use default config.yaml
python -m wallpaper_automator

# Specify config file
python -m wallpaper_automator -c /path/to/config.yaml
```

Or start programmatically from Python:

```python
from wallpaper_automator import run_service
run_service("config.yaml")
```

## Auto Start

To launch automatically at logon, create a **Task Scheduler** task with an **At log on** trigger. Use `pythonw.exe` to hide the console window:

## How Config Parameters Flow to Components

Each component type can accept configuration via a `config` block in the YAML file. The key-value pairs are unpacked as keyword arguments to the component's constructor.

### Triggers

```yaml
trigger:
  - name: time
    config:
      interval: 60       # → TimeTrigger(interval=60)
      times:             # → TimeTrigger(times=["09:00", "18:00"])
        - "09:00"
        - "18:00"
```

| Trigger | Constructor Parameters | Description |
|---------|----------------------|-------------|
| `time` | `interval` (seconds), `times` (list of `"HH:MM"` strings) | Periodic polling interval and/or fixed daily trigger times |
| `network` | *(none)* | Fires on WiFi SSID changes |
| `windows_session` | *(none)* | Fires on lock/unlock/resume |

### Resources

```yaml
# Static — single image
resource:
  custom_name:
    name: static_wallpaper    # Resource type → constructor lookup
    config:
      path: "C:/img.jpg"     # → StaticWallpaper(path="C:/img.jpg", style="fill")
      style: fill

# Dynamic — cycles through multiple images on a timer
  carousel:
    name: dynamic_wallpaper
    config:
      paths:
        - "C:/img1.jpg"
        - "C:/img2.jpg"
      style: fill
      interval: 300           # → DynamicWallpaper(paths=[...], interval=300, random=False)
      random: false
```

| Resource | Constructor Parameters | Description |
|----------|----------------------|-------------|
| `static_wallpaper` | `path` (str), `style` (str), `restore` (bool, default False), `cache_dir` (str, optional) | Static image wallpaper — `restore=True` restores original wallpaper on demount. `cache_dir` overrides the auto-created temp directory. |
| `dynamic_wallpaper` | `paths` (list[str]), `style` (str), `interval` (int, default 300), `random` (bool, default False), `restore` (bool, default False), `cache_dir` (str, optional) | Multi-image cycling wallpaper — `cache_dir` overrides the auto-created temp directory. |

The shorthand form (`black: "C:/img.jpg"`) is expanded to `static_wallpaper` with the string as the `path`.

### Evaluators

Evaluators don't use a `config` block — their parameters are defined inline in the condition:

```yaml
condition:
  wifi_ssid_is: "Company_WiFi"             # param: SSID string
  in_time_range: ["09:00", "18:00"]         # param: [start, end]
  day_of_week_is: [5, 6]                    # param: list[int]  0=Mon ... 6=Sun
```

### Custom Components

When registering a custom component, define `__init__` parameters matching the keys you expect in the YAML `config` block:

```python
class MyTrigger(BaseThreadTrigger):
    def __init__(self, poll_interval: int = 30, endpoint: str = "..."):
        ...
```

## System Tray

After running, the app displays an icon in the system tray:

- **Auto mode**: Resume auto-switching (hides the manual wallpaper options)
- **Pause mode**: Stop automatic rule-based switching. Manual wallpaper selection becomes available.
- **Select wallpaper**: Manually switch to the specified wallpaper (only available while paused)

## Programmatic Usage

You can start the service from Python code using `run_service()`. This is the recommended way when registering custom components.

```python
from wallpaper_automator import run_service

# Start with the default config.yaml
run_service("config.yaml")

# With custom components registered inline
run_service(
    "config.yaml",
    custom_triggers={"my_trigger": MyTrigger},
    custom_resources={"my_resource": MyResource},
    custom_evaluators={"my_evaluator": MyEvaluator()},
)
```

## Custom Components

All three component types are extensible. The recommended way to register custom components is by passing them to `run_service()` via the `custom_triggers`, `custom_resources`, and `custom_evaluators` keyword arguments. All base classes are importable from the top-level `wallpaper_automator` package.

### Custom Resource

Extend `BaseResource` and pass it through `run_service()`.

```python
from wallpaper_automator import BaseResource, run_service

class OnlineResource(BaseResource):
    def __init__(self, query: str = "nature", style: str = "fill"):
        self.query = query
        self.style = style

    def mount(self):
        # Download image, then set as wallpaper
        ...

    def demount(self):
        # Restore previous wallpaper
        ...

run_service("config.yaml", custom_resources={"online": OnlineResource})
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

Extend `BaseTrigger` or `BaseThreadTrigger` and pass it through `run_service()`.

```python
from wallpaper_automator import BaseThreadTrigger, run_service

class UsbPlugTrigger(BaseThreadTrigger):
    def run(self):
        while not self._stop_event.is_set():
            # Poll for USB insertion/removal
            ...
            self.trigger()
            self._stop_event.wait(timeout=5)

run_service("config.yaml", custom_triggers={"usb_plug": UsbPlugTrigger})
```

```yaml
trigger:
  - name: usb_plug
    config: {}
```

### Custom Evaluator

Implement `BaseEvaluator` (a callable interface) and pass an instance through `run_service()`.

For example, here is a geo-location evaluator that checks whether the current machine is within a given radius of a target location:

```python
from wallpaper_automator import BaseEvaluator, run_service

class GeoEvaluator(BaseEvaluator):
    """Evaluate whether the current machine is within a given radius
    of a target location."""

    def __call__(self, param: dict) -> bool:
        # 1. Validate input: param must contain lat, lon, radius
        # 2. Resolve current location via IP geolocation API
        # 3. Compute distance between current location and target
        # 4. Return True if distance <= radius
        ...

run_service("config.yaml", custom_evaluators={"in_geo_range": GeoEvaluator()})
```

The example above resolves the machine's public IP via a geolocation API, computes the distance using the Haversine formula, and returns ``True`` when the machine is within the configured radius. The actual API call and distance calculation are left as an exercise for the reader -- the pattern shown here is the extensibility contract: subclass ``BaseEvaluator``, implement ``__call__``, and pass an instance to ``run_service()``.

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

All custom component types can be registered in a single `run_service()` call:

```python
from wallpaper_automator import (
    BaseResource,
    BaseThreadTrigger,
    BaseEvaluator,
    run_service,
)

class MyResource(BaseResource): ...
class MyTrigger(BaseThreadTrigger): ...
class MyEvaluator(BaseEvaluator): ...

run_service(
    "config.yaml",
    custom_triggers={"my_trigger": MyTrigger},
    custom_resources={"my_resource": MyResource},
    custom_evaluators={"in_geo_range": MyEvaluator()},
)
```

### Alternative: Class-level Registration

As an alternative, you can register components directly on the manager classes before calling `run_service()`. This is useful when the registration must happen before the configuration is loaded (e.g., in a plugin system).

```python
from wallpaper_automator import ResourceManager, RuleEngine, TriggerManager, run_service

ResourceManager.register_resource("online", OnlineResource)
TriggerManager.register_trigger("usb_plug", UsbPlugTrigger)
RuleEngine.register_evaluator("my_evaluator", MyEvaluator())

run_service("config.yaml")
```

## Dependencies

- Python 3.12+
- PyYAML — config file parsing
- Pydantic >=2.0 — config validation & data models
- PySide6 — system tray UI
- pywin32 — Windows wallpaper API (SystemParametersInfo) & session monitoring
- Pillow — image resize/compress for wallpaper caching

## License

MIT
