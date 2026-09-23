"""Bounded, local-only battle samples for the explicit two-VM fixture.

No login, passwords, chat, inventory, room names or full process dumps. Unknown
SDP2P data is sampled only after control parsing and exact VM admission.
"""
from collections import Counter
import struct

GAME_IDS = frozenset((3550,4030,4050,4060,4070,4080,4100,4110,4111,4112,4115,4160,4170,4180,8040,8070,8071))
UDP_CONTROL_IDS = frozenset((1001,1002,1012,1013,1014))


class LabCapture:
    def __init__(self, emit, *, maximum=128, per_shape=4, max_bytes=4096):
        self.emit = emit
        self.maximum, self.per_shape, self.max_bytes = maximum, per_shape, max_bytes
        self.counts = Counter()
        self.total = 0
        self.limit_reported = False

    def record(self, source, direction, ident, payload, phase):
        if source == 'game-tcp':
            if ident not in GAME_IDS or phase not in ('room','loading','wait_ready','battle'):
                return
        elif source == 'sdp2p-udp':
            if ident in UDP_CONTROL_IDS or phase not in ('room','loading','wait_ready','battle'):
                return
        else:
            return
        if direction not in ('rx','tx') or len(payload) > self.max_bytes:
            return
        battle_id=struct.unpack_from('<I',payload)[0] if source=='game-tcp' and ident==8071 and len(payload)>=39 else None
        shape = (source,direction,ident,len(payload),battle_id)
        # Do not accumulate an unbounded map of arbitrary UDP identifiers.
        if self.total >= self.maximum:
            if not self.limit_reported:
                self.emit('lab_capture_limit',maximum=self.maximum)
                self.limit_reported=True
            return
        if self.counts[shape] >= self.per_shape:
            return
        self.counts[shape] += 1
        self.total += 1
        self.emit('lab_wire_sample',source=source,direction=direction,id=ident,
                  phase=phase,size=len(payload),sample=self.counts[shape],payload_hex=payload.hex(),
                  **({'battle_id':battle_id} if battle_id is not None else {}))
