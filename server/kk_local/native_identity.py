"""Kernel-attributed loopback peers for the LOCAL Windows client bridge.

This is not LAN authentication. Remote peers and ambiguous/reused process IDs
are rejected. No password or SDK session bytes are read from another process.
"""
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
import socket
import struct


@dataclass(frozen=True)
class NativeIdentity:
    pid: int
    creation: int
    image: str


class WindowsNativeVerifier:
    def __init__(self, image):
        if os.name!='nt':
            raise RuntimeError('native password bridge requires Windows loopback')
        self.image=os.path.normcase(os.path.abspath(image))
        self.iphlp=ctypes.WinDLL('iphlpapi',use_last_error=True)
        self.kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        self.kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        self.kernel.OpenProcess.restype=wintypes.HANDLE
        self.kernel.CloseHandle.argtypes=[wintypes.HANDLE]
        self.kernel.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
        self.kernel.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)]
        for name in ('GetExtendedTcpTable','GetExtendedUdpTable'):
            fn=getattr(self.iphlp,name)
            fn.argtypes=[ctypes.c_void_p,ctypes.POINTER(wintypes.DWORD),wintypes.BOOL,wintypes.ULONG,ctypes.c_int,wintypes.ULONG]
            fn.restype=wintypes.DWORD

    def process(self, pid):
        if type(pid) is not int or not 0<pid<=0xffffffff:
            raise ValueError('invalid native PID')
        handle=self.kernel.OpenProcess(0x1000,False,pid)
        if not handle:
            raise ValueError('native process unavailable')
        try:
            size=wintypes.DWORD(32768)
            image=ctypes.create_unicode_buffer(size.value)
            if not self.kernel.QueryFullProcessImageNameW(handle,0,image,ctypes.byref(size)):
                raise ValueError('native image unavailable')
            path=os.path.normcase(os.path.abspath(image.value))
            if path!=self.image:
                raise ValueError('native image mismatch')
            values=[wintypes.FILETIME() for _ in range(4)]
            if not self.kernel.GetProcessTimes(handle,*(ctypes.byref(v) for v in values)):
                raise ValueError('native creation time unavailable')
            created=(values[0].dwHighDateTime<<32)|values[0].dwLowDateTime
            return NativeIdentity(pid,created,path)
        finally:
            self.kernel.CloseHandle(handle)

    def _table(self, protocol):
        fn=getattr(self.iphlp,'GetExtendedTcpTable' if protocol=='tcp' else 'GetExtendedUdpTable')
        kind=5 if protocol=='tcp' else 1
        width=24 if protocol=='tcp' else 12
        size=wintypes.DWORD()
        if fn(None,ctypes.byref(size),False,socket.AF_INET,kind,0) not in (0,122):
            raise ValueError('endpoint table unavailable')
        for _ in range(3):
            if not 4<=size.value<=16*1024*1024:
                raise ValueError('endpoint table size limit')
            buf=ctypes.create_string_buffer(size.value)
            result=fn(buf,ctypes.byref(size),False,socket.AF_INET,kind,0)
            if result==122:
                continue
            if result:
                raise ValueError('endpoint table query failed')
            count=struct.unpack_from('<I',buf.raw)[0]
            if 4+count*width>size.value:
                raise ValueError('endpoint table truncated')
            return [struct.unpack_from('<'+'I'*(width//4),buf.raw,4+i*width) for i in range(count)]
        raise ValueError('endpoint table unstable')

    def tcp_peer(self, peer, local):
        if peer[0]!='127.0.0.1' or local[0]!='127.0.0.1':
            raise ValueError('non-loopback authentication peer')
        address=struct.unpack('<I',socket.inet_aton('127.0.0.1'))[0]
        owners={r[5] for r in self._table('tcp') if r[0]==5 and r[1]==address and r[3]==address
                and socket.ntohs(r[2]&0xffff)==peer[1] and socket.ntohs(r[4]&0xffff)==local[1]}
        if len(owners)!=1:
            raise ValueError('native TCP ownership ambiguous')
        return self.process(owners.pop())

    def udp_peer(self, peer):
        if peer[0]!='127.0.0.1':
            raise ValueError('non-loopback UDP peer')
        address=struct.unpack('<I',socket.inet_aton('127.0.0.1'))[0]
        owners={r[2] for r in self._table('udp') if r[0] in (0,address) and socket.ntohs(r[1]&0xffff)==peer[1]}
        if len(owners)!=1:
            raise ValueError('native UDP ownership ambiguous')
        return self.process(owners.pop())
