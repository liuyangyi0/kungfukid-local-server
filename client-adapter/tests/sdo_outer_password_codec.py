"""Compatibility import; implementation lives in server.kk_local.sdo_outer."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.kk_local.sdo_outer import derive_key, wrap_outer, unwrap_outer, _IV
