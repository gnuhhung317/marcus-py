import sys
import os

# Ensure repository root is on sys.path so top-level packages like `examples` are importable
_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _root not in sys.path:
    sys.path.insert(0, _root)
