"""Compatibility import; implementation lives in server.kk_local.sdo_password."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from server.kk_local.sdo_password import PasswordBridge, checksum, decode_inner
