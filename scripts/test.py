from wallpaper_automator.models import ConditionNode, ConfigModel
import io

import yaml

stream = io.StringIO(
"""
# 1. 资源池：解耦路径与逻辑
resource:
  office_view:
    name: static_wallpaper
    config:
      path: "C:/Users/ASUS/Pictures/8_.png"
      style: fill
  black: "C:/Users/ASUS/Pictures/black.jpg"

trigger:
  - name: windows_session_change

  - name: network_change

rule:
  - name: "office_mode"
    condition:
      or:
        - network: "Company_WiFi"
        - and:
            - location: { lat: 31.23, lon: 121.47, radius: 0.5 }
            - workday_only: true
    target: "office_view"

  - name: "night_mode"
    condition:
      time_range: ["23:00", "05:00"]
    target: "black"

fallback: "office_view"
"""    
)

c = yaml.safe_load(stream)

# 测试

# node = ConditionNode(**c)
node = ConfigModel(**c)
print(node)

