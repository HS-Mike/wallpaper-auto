# Wallpaper Automator

Automatically switches Windows desktop wallpapers based on configurable conditions (time, WiFi, location, workday).

## Features

- **Multi-condition triggers**: Supports Windows session changes, network changes, and time changes as three types of triggers
- **Flexible rules**: Supports AND/OR condition combinations, can evaluate multiple conditions simultaneously
- **Condition types**:
  - `network`: Current connected WiFi name
  - `location`: GPS coordinates (via IP geolocation)
  - `workday_only`: Whether it's a workday
  - `time_range`: Time range (supports crossing midnight)
- **System tray control**: System tray menu for manual wallpaper switching and pause/resume auto-switching
- **Thread-safe**: Each monitoring module runs independently without blocking others

## Installation

```bash
pip install -e .
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
    name: static_wallpaper                # Resource type (extensible)
    config:
      path: "C:/path/to/wallpaper.jpg"
      style: fill                         # fill / fit / stretch / center / tile

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
        - is_today_workday: true
    target: "dark_wallpaper"
  - name: "Traveling"
    condition:
      in_geo_range: { lat: 31.23, lon: 121.47, radius: 0.5 }
    target: "travel_wallpaper"

# 4. Fallback wallpaper (used when no rule matches)
fallback: "default_wallpaper"
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
resource:
  custom_name:
    name: static_wallpaper    # Resource type → constructor lookup
    config:
      path: "C:/img.jpg"     # → StaticWallpaper(path="C:/img.jpg", style="fill")
      style: fill
```

| Resource | Constructor Parameters | Description |
|----------|----------------------|-------------|
| `static_wallpaper` | `path` (str), `style` (str) | Static image wallpaper |

The shorthand form (`black: "C:/img.jpg"`) is expanded to `static_wallpaper` with the string as the `path`.

### Evaluators

Evaluators don't use a `config` block — their parameters are defined inline in the condition:

```yaml
condition:
  wifi_ssid_is: "Company_WiFi"             # param: SSID string
  in_time_range: ["09:00", "18:00"]         # param: [start, end]
  in_geo_range: { lat: 31.23, lon: 121.47, radius: 0.5 }  # param: dict
  is_today_workday: true                    # param: bool
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

- **Select wallpaper**: Manually switch to the specified wallpaper
- **Auto mode**: Resume auto-switching
- **Pause**: Stop auto-switching

## Custom Components

All three component types are extensible. Register custom implementations before starting `WallpaperController`.

### Custom Resource

Extend `BaseResource` and register it so the config loader can instantiate it by name.

```python
from wallpaper_automator.resource.base_resource import BaseResource
from wallpaper_automator.resource_manager import ResourceManager

class UnsplashResource(BaseResource):
    def __init__(self, query: str = "nature", style: str = "fill"):
        self.query = query
        self.style = style
        super().__init__(temp_dir=True)

    def mount(self):
        # Download image from Unsplash API to self.cache_dir, then set as wallpaper
        ...

    def demount(self):
        # Restore previous wallpaper
        ...

# Register before starting
ResourceManager.register_resource("unsplash", UnsplashResource)
```

```yaml
resource:
  daily:
    name: unsplash
    config:
      query: "mountain"
      style: fill
```

### Custom Trigger

Extend `BaseTrigger` or `BaseThreadTrigger` and register it.

```python
from wallpaper_automator.trigger.base_trigger import BaseThreadTrigger
from wallpaper_automator.trigger_manager import TriggerManager

class UsbPlugTrigger(BaseThreadTrigger):
    def run(self):
        while not self.stop_event.is_set():
            # Poll for USB insertion/removal
            ...
            self.trigger()
            self.stop_event.wait(timeout=5)

TriggerManager.register_trigger("usb_plug", UsbPlugTrigger)
```

```yaml
trigger:
  - name: usb_plug
    config: {}
```

### Custom Evaluator

Implement `BaseEvaluator` (a callable interface) and register it with the rule engine.

```python
from wallpaper_automator.evaluator.base_evaluator import BaseEvaluator
from wallpaper_automator.rule_engine import RuleEngine

class BatteryLevelEvaluator(BaseEvaluator):
    def __call__(self, param: dict) -> bool:
        # param: {"below": 20}
        current = get_battery_percent()
        return current < param["below"]

RuleEngine.register_evaluator("battery_below", BatteryLevelEvaluator())
```

```yaml
rule:
  - name: "Low battery"
    condition:
      battery_below: { below: 20 }
    target: "power_saver_wallpaper"
```

## Project Structure

```
src/wallpaper_automator/
├── __main__.py                # Entry point — CLI arg parsing, launches controller
├── init_config.py             # Starter config template generator (init-config subcommand)
├── wallpaper_controller.py    # Orchestrator — owns worker loop, routes trigger → rule → resource
├── config_store.py            # YAML config loader & validator (Pydantic)
├── models.py                  # Pydantic data models for config, conditions, rules
├── task.py                    # Task queue types (ModeSwitch / ResourceSet / Quit)
├── rule_engine.py             # Recursive AND/OR condition tree evaluation
├── system_tray.py             # PySide6 system tray UI with Qt signal bridge
├── resource_manager.py        # Resource lifecycle (mount/demount), registration
├── trigger_manager.py         # Trigger lifecycle, pause/resume, callback routing
│
├── trigger/                   # Trigger implementations (monitor system events)
│   ├── base_trigger.py        #   BaseTrigger & BaseThreadTrigger abstract classes
│   ├── network_trigger.py     #   WMI-based WiFi / network change detection
│   ├── time_trigger.py        #   Fixed-time / interval-based triggering
│   └── windows_session_trigger.py  # Win32 session event (lock/unlock/logon) monitor
│
├── evaluator/                 # Condition evaluator implementations
│   ├── base_evaluator.py      #   BaseEvaluator callable interface
│   ├── wifi_ssid_evaluator.py #   netsh-based WiFi SSID matching
│   ├── workday_evaluator.py   #   Holiday API (timor.tech) workday detection
│   ├── time_range_evaluator.py#   Time range check (supports midnight crossing)
│   └── geo_evaluator.py       #   IP geolocation + Haversine distance check
│
├── resource/                  # Wallpaper resource implementations
│   ├── base_resource.py       #   BaseResource abstract class with temp cache mgmt
│   └── static_wallpaper.py    #   Static image wallpaper (win32 SPI) with auto-compress
│
└── util/                      # Shared utilities
    └── callback_register.py   #   Thread-safe callback registry mixin
```

## Dependencies

- Python 3.9+
- PyYAML — config file parsing
- Pydantic >=2.0 — config validation & data models
- PySide6 — system tray UI
- pywin32 — Windows wallpaper API (SystemParametersInfo) & session monitoring
- Pillow — image resize/compress for wallpaper caching
- requests — IP geolocation & workday API calls
- tenacity — retry logic for API calls
- wmi — WMI-based network change monitoring
- fake-useragent — disguises API requests

## License

MIT
