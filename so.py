import sys, os, importlib
_so_path = os.path.dirname(__file__)
if _so_path not in sys.path:
    sys.path.append(_so_path)
import guest_access, smmboss, gdb_guest
importlib.reload(guest_access)
importlib.reload(smmboss)
importlib.reload(gdb_guest)
gdb_guest.add_niceties()
from gdb_guest import guest
for (_attr, _attrval) in guest.world.__dict__.items():
    if not _attr.startswith('_'):
        globals()[_attr] = _attrval
