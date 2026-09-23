"""Compatibility import; implementation lives in server.kk_local.sdo_wire."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.kk_local.sdo_wire import response, guid_response, authentication_result
