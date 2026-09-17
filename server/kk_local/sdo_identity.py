"""Attribute a local SDK HTTP peer to its live game parent, without memory reads."""
import ctypes
from ctypes import wintypes as w
from .native_identity import WindowsNativeVerifier


class ProcessEntry(ctypes.Structure):
    _fields_=[('size',w.DWORD),('usage',w.DWORD),('pid',w.DWORD),('heap',ctypes.c_size_t),
              ('module',w.DWORD),('threads',w.DWORD),('parent',w.DWORD),
              ('priority',w.LONG),('flags',w.DWORD),('exe',w.WCHAR*260)]


class WindowsSdkVerifier:
    def __init__(self, sdk_image, game_verifier):
        self.sdk=WindowsNativeVerifier(sdk_image);self.game=game_verifier
        self.kernel=self.sdk.kernel
        self.kernel.CreateToolhelp32Snapshot.argtypes=[w.DWORD,w.DWORD]
        self.kernel.CreateToolhelp32Snapshot.restype=w.HANDLE
        for name in ('Process32FirstW','Process32NextW'):
            fn=getattr(self.kernel,name);fn.argtypes=[w.HANDLE,ctypes.POINTER(ProcessEntry)];fn.restype=w.BOOL

    def parent_pid(self, pid):
        handle=self.kernel.CreateToolhelp32Snapshot(2,0)
        if handle in (None,ctypes.c_void_p(-1).value): raise ValueError('snapshot unavailable')
        try:
            row=ProcessEntry();row.size=ctypes.sizeof(row)
            ok=self.kernel.Process32FirstW(handle,ctypes.byref(row))
            while ok:
                if row.pid==pid:return int(row.parent)
                ok=self.kernel.Process32NextW(handle,ctypes.byref(row))
            raise ValueError('SDK absent')
        finally:self.kernel.CloseHandle(handle)

    def peer(self, peer, local):
        sdk=self.sdk.tcp_peer(peer,local)
        game=self.game.process(self.parent_pid(sdk.pid))
        if sdk.creation<game.creation or self.sdk.process(sdk.pid)!=sdk:
            raise ValueError('SDK parent identity changed')
        return sdk,game

    def recheck(self, pair):
        sdk,game=pair
        if self.sdk.process(sdk.pid)!=sdk or self.game.process(game.pid)!=game or self.parent_pid(sdk.pid)!=game.pid:
            raise ValueError('SDK/game identity changed')
