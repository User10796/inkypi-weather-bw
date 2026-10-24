"""Lets the plugin import on any machine, without InkyPi, a display or network.

``weather_bw.py`` imports ``plugins.base_plugin.base_plugin.BasePlugin``
from InkyPi; a minimal stand-in is registered here instead.
"""

import os
import sys
import types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for path in (ROOT, os.path.dirname(os.path.abspath(__file__))):
    if path not in sys.path:
        sys.path.insert(0, path)


class _StubBasePlugin:
    def __init__(self, config, **dependencies):
        self.config = config

    def generate_settings_template(self):
        return {"settings_template": "weather_bw/settings.html"}


if "plugins.base_plugin.base_plugin" not in sys.modules:
    for name in ("plugins", "plugins.base_plugin", "plugins.base_plugin.base_plugin"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["plugins.base_plugin.base_plugin"].BasePlugin = _StubBasePlugin
