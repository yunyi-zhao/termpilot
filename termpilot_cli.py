"""Import shim so the console can reuse start_supervisor() from termpilot.py."""
import importlib.util
import os

_spec = importlib.util.spec_from_file_location("_termpilot_main", os.path.join(os.path.dirname(os.path.abspath(__file__)), "termpilot.py"))
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
start_supervisor = _mod.start_supervisor
setup_interactive = _mod.setup_interactive
