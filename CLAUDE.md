# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Wallpaper Automator automatically switches Windows desktop wallpapers based on configurable conditions (time, location, WiFi network, workday). It runs as a system tray application using PySide6.

## Commands

```bash
# Run the application
python -m wallpaper_automator
python -m wallpaper_automator -c /path/to/config.yaml

# Install in dev mode
pip install -e ".[dev]"

# Build executable (PyInstaller)
pip install pyinstaller
pyinstaller --onefile --windowed --name WallpaperAutomator src/wallpaper_automator/__main__.py

# Run tests
pytest                                                        # all tests with coverage
pytest -k "TestRuleEngine"                                     # single test class
pytest tests/unit/test_rule_engine.py                          # single file
pytest --co                    # print which lines aren't covered
pytest -x --tb=long            # stop on first failure with full traceback
pytest -x --tb=long --capture=no  # stop on first failure with full I/O

# Type checking & linting
mypy src/                                                     # static type checking
ruff check src/ tests/                                         # lint
ruff format --check src/ tests/                                # format check
```

## Code Style

### Naming Conventions
- **Classes**: `PascalCase` — `WallpaperController`, `NetworkTrigger`, `StaticWallpaper`
- **Functions & methods**: `snake_case` — `evaluate_rules()`, `register_trigger()`
- **Private members**: underscore prefix — `_worker_loop()`, `_active_resource`
- **Constants**: `UPPER_SNAKE_CASE` — `DEFAULT_CONFIG_PATH`
- **Files**: `snake_case.py` — `wallpaper_controller.py`, `rule_engine.py`
- **Tests**: `test_<module>.py` — `test_rule_engine.py`, `test_network_trigger.py`
- **Type aliases**: `PascalCase` with descriptive names — `ConditionResult`, `EvaluatorFunc`

### Library Preferences
- **Validation**: Pydantic `BaseModel` for all config/data models; frozen models (`model_config = {"frozen": True}`) for task types
- **GUI**: PySide6 (Qt6 bindings) only — not PyQt
- **Testing**: pytest with built-in `tmp_path` fixture (not tempfile); `pytest-cov` for coverage
- **Async**: Prefer `threading.Thread` + `queue.Queue` over `asyncio` for background work
- **Thread safety**: `threading.Event` for signaling, `queue.Queue` for producer-consumer, `CallbackRegister` as a mixin for callback management

### Imports
Order: 1) standard library, 2) third-party, 3) local. Group with a blank line between.

```python
import os
import threading
from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel
from PySide6.QtGui import QAction

from wallpaper_automator.models import ConfigModel
from wallpaper_automator.rule_engine import RuleEngine
```

### Typing
- Full type annotations on all function signatures (parameters + return)
- `Optional[X]` rather than `X | None` for consistency
- `from __future__ import annotations` at top of files to avoid circular imports
- Use `Protocol` for duck typing interfaces instead of ABC when no runtime dispatch is needed

## Project Structure

```
wallpaper_automator/
├── src/wallpaper_automator/
│   ├── __init__.py
│   ├── __main__.py              # entry point
│   ├── wallpaper_controller.py  # orchestrator, owns managers & worker loop
│   ├── config_store.py          # YAML loading & validation via Pydantic
│   ├── rule_engine.py           # AND/OR condition tree evaluation
│   ├── trigger_manager.py       # trigger lifecycle (start/stop/pause)
│   ├── resource_manager.py      # wallpaper mount/demount lifecycle
│   ├── system_tray.py           # PySide6 system tray app + bridge
│   ├── models.py                # Pydantic config models
│   ├── task.py                  # Queue task types (QuitTask, ModeSwitchTask, etc.)
│   ├── callback_register.py     # Thread-safe callback mixin
│   ├── trigger/
│   │   ├── base_trigger.py      # BaseTrigger / BaseThreadTrigger
│   │   ├── network_trigger.py   # WiFi network change detection
│   │   ├── time_trigger.py      # Periodic time-based checks
│   │   └── windows_session_trigger.py  # Lock/unlock/wake events
│   ├── evaluator/
│   │   ├── base_evaluator.py    # BaseEvaluator
│   │   ├── wifi_ssid_evaluator.py
│   │   ├── workday_evaluator.py
│   │   ├── time_range_evaluator.py
│   │   └── geo_evaluator.py
│   └── resource/
│       ├── base_resource.py     # BaseResource
│       └── static_wallpaper.py  # Apply/restore wallpapers
├── tests/
│   ├── unit/                    # unit tests (pytest)
│   └── conftest.py              # shared fixtures
├── scripts/                     # utility/demo scripts
└── CLAUDE.md
```

## Architecture

### Component Model

The app has three pluggable component types, each with a base class and built-in implementations:

| Component | Base Class | Built-in Implementations | Purpose |
|-----------|-----------|-------------------------|---------|
| **Trigger** | `trigger/base_trigger.py::BaseTrigger` / `BaseThreadTrigger` | `NetworkTrigger`, `TimeTrigger`, `WindowsSessionTrigger` | Monitor system events and fire callbacks |
| **Evaluator** | `evaluator/base_evaluator.py::BaseEvaluator` | `WIFISsidEvaluator`, `WorkdayEvaluator`, `TimeRangeEvaluator`, `GeoEvaluator` | Check a single condition and return bool |
| **Resource** | `resource/base_resource.py::BaseResource` | `StaticWallpaper` | Apply/demount a wallpaper |

Custom implementations can be registered at runtime via the `register_trigger`/`register_resource` class methods on `TriggerManager`/`ResourceManager`.

### Control Flow

```
__main__.py
  └─ WallpaperController (wallpaper_controller.py)
       ├─ ConfigStore (config_store.py)         — loads & validates YAML via Pydantic
       ├─ RuleEngine (rule_engine.py)           — evaluates AND/OR condition trees
       ├─ TriggerManager (trigger_manager.py)   — manages trigger lifecycle, pause/resume
       │    └─ BaseTrigger instances            — each runs in its own thread
       ├─ ResourceManager (resource_manager.py) — manages wallpaper mount/demount
       │    └─ BaseResource instances
       └─ WallpaperSwitchSystemTray (system_tray.py) — PySide6 system tray UI
            └─ SystemTrayBridge                 — thread-safe signal/slot bridge to controller
```

The controller runs a **worker loop** on a dedicated thread that processes tasks from a `queue.Queue`:
- `ModeSwitchTask`: toggle between AUTO and MANUAL mode
- `ResourceSetTask`: demount current resource, mount new one
- `QuitTask`: graceful shutdown

When a trigger fires, it calls back through `TriggerManager → WallpaperController.evaluate()`, which runs the `RuleEngine` and enqueues a `ResourceSetTask` if the evaluated result differs from the active resource.

### Data Models (`models.py`)

- `ConfigModel`: top-level YAML schema with resource/trigger/rule/fallback fields
- `ConditionNode`: recursive AND/OR condition tree with leaf evaluator nodes
- `Task` types (`task.py`): discriminated union of `QuitTask | ModeSwitchTask | ResourceSetTask`

### Configuration

YAML config with three sections:
1. `resource`: wallpaper pool (path + style)
2. `trigger`: enable/disable trigger types (network_change, time_change, windows_session_change)
3. `rule`: ordered rules with conditions and target wallpaper ID
4. `fallback`: default wallpaper when no rule matches

Rules are evaluated in order; the first matching rule's target is applied. Condition trees support `and`/`or` combinators with leaf nodes for `wifi_ssid_is`, `is_today_workday`, `in_time_range`, `in_geo_range`.

### Threading

- Each `BaseThreadTrigger` runs in its own daemon thread
- `TriggerManager` can pause (suppress callbacks without stopping threads) via a `threading.Event`
- `SystemTrayBridge` uses Qt signals for thread-safe UI updates
- The controller's worker loop is the single consumer of the task queue (`Mode`, `ResourceSet`)
- `CallbackRegister` provides thread-safe callback registration/triggering

### Key Conventions

- Pydantic `BaseModel` for all config/data validation (frozen models for task types)
- `CallbackRegister` is used as a mixin for trigger callbacks
- `WallpaperController` owns all lifecycle — no external mutation of managers
- `StaticWallpaper.mount()` saves the current wallpaper so `demount()` can restore it
- Image compression caching uses a process-level temp directory cleaned via `atexit`
