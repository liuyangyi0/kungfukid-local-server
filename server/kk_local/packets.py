"""VM-qualified packet shapes, with server-only choices explicitly provisional.

Native refs: 046B:A27050, 047F:823420, 0C1C:824490, 0C58:8202C0,
0FF0:81E1A0, 1054:829770, 1F86:82C4E0. See room/P2P experiment.
"""
import struct
from .wire import Message, ProtocolError, text_be
from .shop_catalog import encode_catalog

COMPETITIVE_MODES = frozenset((0, 1, 2, 3))
TEAM_MODES = frozenset((1, 3))


class RoomRequestRejected(ProtocolError):
    """Supported framing but unsupported local room policy; keep GS alive."""
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def room_rejection(reason=None):
    """3030/824FB0, native DBC96C table (same130 texts as12E2824)."""
    code=130 if reason is None else 114  # unknown legacy call / creation failed
    if reason in ('unknown_map_id','unsupported_room_map','map_resources_missing'):
        code=44
    return Message(3030,struct.pack('<H',code))


def equipment_rejection(reason):
    # 2100/A29E30: only these Store causes are qualified, not arbitrary codes.
    code=72 if reason=='item not owned' else 38
    return Message(2100,struct.pack('<H',code))


def login_ack(account: str, uid: int):
    # SDK vtable48=account, vtable52=numeric UID; must not reverse these.
    return Message(1002, b'\1' + text_be(account) + text_be(str(uid)) + bytes(range(1, 49)))


def login_directory(port: int):
    meta = bytearray(44)
    struct.pack_into('<H', meta, 0, 100)
    struct.pack_into('<II', meta, 12, 1, 999)
    meta[24:35] = b'Local Lobby'
    payload = struct.pack('>HI', 1, 1) + b'\1\0\0\x7f' + struct.pack('>HHBB', port, 0, 0, 44)
    return Message(1012, payload + meta)


def catalog(port: int):
    endpoint = struct.pack('<IH20s', 1, port, b'127.0.0.1')
    lobby = bytearray(46)
    struct.pack_into('<I', lobby, 0, 1)
    lobby[4:15] = b'Local Lobby'
    struct.pack_into('<H', lobby, 25, 100)
    struct.pack_into('<I', lobby, 29, 1)
    struct.pack_into('<I', lobby, 42, 999)
    return [Message(7080, endpoint), Message(7070, bytes(lobby))]


def account_packets(inventory: bytes, port: int, *, gold=0):
    config = bytearray(44)
    struct.pack_into('<III', config, 0, 10, 5, 255)
    struct.pack_into('<I', config, 20, 1)  # LOCAL mark threshold, original unknown
    core = bytearray(37)
    struct.pack_into('<I', core, 12, 1)
    if type(gold) is not int or not 0<=gold<=2147483647:raise ProtocolError('gold balance range')
    struct.pack_into('<I',core,16,gold)
    return [Message(1131, bytes(config)), Message(1020, bytes(core)),
            Message(1120, inventory), *catalog(port)]


def lobby_context(udp_port: int):
    p = bytearray(52)
    struct.pack_into('<I20sHI', p, 0, 1, b'127.0.0.1', udp_port, 1)
    return Message(2030, bytes(p))


def resolve_room_request(request: bytes, map_catalog=None):
    if len(request) != 81:
        raise ProtocolError('room creation length')
    # choosemode.xml and native98C1B0: the same room-entry contract selects
    # four competitive factories, or5 for practice. This is admission, not
    # a claim that competitive start/settlement has been qualified.
    if request[46] not in COMPETITIVE_MODES and request[46] != 5:
        raise RoomRequestRejected('unsupported_room_mode')
    chosen, resolved = struct.unpack_from('<ii', request, 38)
    if request[37] not in (2, 4, 6, 8):
        raise RoomRequestRejected('unsupported_room_capacity')
    if map_catalog is not None:
        from .maps import MapAdmissionError
        try:chosen,resolved=map_catalog.resolve(request[46],request[37],chosen,resolved)
        except MapAdmissionError as exc:raise RoomRequestRejected(str(exc)) from None
    else:
        # Explicit legacy fixture without a client source stays water4-only.
        if chosen <= 0:chosen = resolved = 804
        elif resolved <= 0:resolved = chosen
        if (chosen,resolved)!=(804,804):raise RoomRequestRejected('unsupported_room_map')
    normalized=bytearray(request);struct.pack_into('<ii',normalized,38,chosen,resolved)
    return bytes(normalized)


def update_room_request(previous, update, *, map_catalog=None):
    """7F9F40 maps3200/48 into creation81; 8205B0 consumes3220/48.

    Local admission reuses supported map/mode/capacity rules. Updates do not
    change the room's existing mode/capacity or publish its password in lists.
    """
    if len(previous)!=81 or len(update)!=48:raise ProtocolError('room update length')
    if update[8] or update[11] or any(update[i] not in (0,1) for i in (9,10,12,13)):
        raise RoomRequestRejected('unsupported_room_option')
    for field in (update[14:35],update[35:46]):
        text,sep,tail=field.partition(b'\0')
        if not sep or any(tail):raise RoomRequestRejected('invalid_room_text')
        try:decoded=text.decode('gbk')
        except UnicodeDecodeError:raise RoomRequestRejected('invalid_room_text') from None
        if any(ord(ch)<32 or ord(ch)==127 for ch in decoded):raise RoomRequestRejected('invalid_room_text')
    if not update[14]:raise RoomRequestRejected('empty_room_name')
    if struct.unpack_from('<H',update,46)[0] not in (120,180,240,300):
        raise RoomRequestRejected('unsupported_room_duration')
    candidate=bytearray(previous)
    candidate[:21]=update[14:35];candidate[21:32]=update[35:46]
    candidate[32:37]=update[8:11]+update[12:14]
    candidate[38:46]=update[:8];candidate[47:49]=update[46:48]
    resolved=resolve_room_request(bytes(candidate),map_catalog)
    reply=bytearray(update);reply[:8]=resolved[38:46]
    return resolved,Message(3220,bytes(reply))


def room_entry(request: bytes, uid: int, room_id: int = 1, *, map_catalog=None):
    request=resolve_room_request(request,map_catalog)
    chosen,resolved=struct.unpack_from('<ii',request,38)
    p = bytearray(245)
    struct.pack_into('<HQBBii', p, 0, room_id, uid, 0, 0, chosen, resolved)
    p[24:45], p[45:56] = request[:21], request[21:32]
    p[56:61] = request[32:37]
    p[61], p[62], p[65] = int(request[21] != 0), request[37], request[46]
    p[67:69], p[69:73], p[73] = request[47:49], request[50:54], request[49]
    p[78:82] = request[59:63]
    struct.pack_into('<Q', p, 96, uid)
    # Remaining optional fields retain fixture's explicitly local defaults.
    return bytes(p)


def battle_start(room_id: int, runtime_value: int, serial: int):
    p = bytearray(53)
    struct.pack_into('<I', p, 0, room_id)
    struct.pack_into('<I', p, 5, serial)
    struct.pack_into('<I', p, 13, runtime_value)
    struct.pack_into('<II', p, 45, room_id, serial)
    return bytes(p)


def battle_ready(room_id: int, serial: int):
    return struct.pack('<III', room_id, room_id, serial)


def empty_local_catalog(category: int, variant: int):
    """9070 ->9080: A2D910 consumes2 selector bytes + count32 + N*108.

    The local service has no configured sale catalog yet. Return its truthful
    empty state, not invented prices/items, and let the native cache finish.
    This does not implement a populated shop or its purchase protocol.
    """
    if any(type(x) is not int or not 0<=x<=255 for x in (category,variant)):
        raise ProtocolError('catalog selector range')
    return encode_catalog(category,variant)


def purchase_unavailable():
    """9060/A2A200: u16>=130 selects the native generic failure branch.

    No sale catalog is configured. This is a local refusal, not a claimed
    original-server reason code or a successful purchase acknowledgement.
    """
    return Message(9060,struct.pack('<H',130))


def nickname_changed(uid, old, new):
    # 827140 consumes identity+25 and21-byte name+33. Prefix is local result/old name.
    names=[x.encode('gbk') for x in (old,new)]
    if any(not x or len(x)>20 or b'\0' in x for x in names):raise ProtocolError('nickname response width')
    return Message(9007,struct.pack('<I',0)+names[0].ljust(21,b'\0')+
                   struct.pack('<Q',uid)+names[1].ljust(21,b'\0'))


def nickname_rejected():
    # 827050 requires54 bytes and uses first i32;130 selects generic text.
    return Message(9008,struct.pack('<I',130)+bytes(50))


def gold_purchase_result(balance, item_record, catalog_record):
    """After committed transaction: absolute balance, inventory upsert, UI ack."""
    if not 0<=balance<=2147483647 or (item_record is not None and len(item_record)!=68) or len(catalog_record)!=108:
        raise ProtocolError('invalid committed purchase result')
    return [Message(1240,struct.pack('<I',balance))]+([Message(2160,bytes(item_record))] if item_record is not None else [])+[Message(9050,bytes(catalog_record))]


def no_local_ranked_season():
    """20370 ->821890 ->882C20/882770: zero first word selects no-data UI.

    Only that branch is implemented: unused prefix bytes are local zero-fill,
    not claimed original reserved fields. Text begins at36 and is terminated.
    Do not fabricate ranked wins, points, ranks or historical seasons.
    """
    return Message(20370,bytes(36)+b'No local ranked season is configured.\0')


def inactive_wealth_page(page):
    """Native 20562 no-entry branch; explicitly no configured local event.

    A2BD10 copies878; 869C30 hides rows with zero presence keys;86AB40
    reads a terminated refresh caption at+4. No invented spending or prizes.
    """
    if type(page) is not int or not 0<=page<=10:raise ProtocolError('wealth page range')
    caption=b'Local wealth event not configured'
    return Message(20562,struct.pack('<I',page)+caption.ljust(64,b'\0')+bytes(810))


def inactive_wealth_self():
    # 86AB40 displays the out-of-top100 branch for a negative native rank.
    return Message(20564,struct.pack('<i',-1)+bytes(64))


def training_status(uid: int, minutes: int, active: bool, *, rewards=False):
    # 21001/21005: A2AFE0/A2AEB0 consume32+24, copied by7F5540.
    # Tier/boxes stay zero: no premium or chest grant. Optional hourly rates
    # are explicitly PROVISIONAL local policy, not recovered server parameters.
    policy = [0,100,2400,0,0,0] if rewards else [0]*6
    return struct.pack('<Q6I6I', uid, 0, 0, 0, minutes, 0, int(active), *policy)


def consumable_snapshot(uid, slots):
    # 81F7E0 consumes34-byte rows. Unread gaps stay zero in this local adapter.
    out=bytearray(34)
    struct.pack_into('<Q',out,0,uid)
    for slot,inst_at,secondary_at,count_at in ((27,8,18,26),(28,12,22,30)):
        r=slots[slot]
        struct.pack_into('<I',out,inst_at,struct.unpack_from('<I',r)[0])
        struct.pack_into('<I',out,secondary_at,struct.unpack_from('<I',r,9)[0])
        struct.pack_into('<H',out,count_at,struct.unpack_from('<H',r,23)[0])
    return Message(4210,bytes(out))


def equipped_records(inventory):
    if len(inventory) % 68:
        raise ProtocolError('inventory record alignment')
    return [inventory[i:i+68] for i in range(0,len(inventory),68)
            if struct.unpack_from('<H',inventory,i+17)[0]]


def fighter_snapshot(uid, nickname, profile, inventory, slot, team, p2p_id, *, ready=False, update=False):
    """81CF10/81EEA0:149-byte fighter then its ACTUAL equipped68-byte rows.

    Optional guild/rank/premium fields use local non-premium defaults. This
    is a provisional server record, not a recovered old-server serializer.
    """
    if len(profile)!=360 or not 0<=slot<8 or team not in (0,1):
        raise ProtocolError('fighter identity/slot')
    name=nickname.encode('gbk')
    if not name or len(name)>20 or b'\0' in name:
        raise ProtocolError('fighter nickname')
    records=equipped_records(inventory)
    if len(records)>255:
        raise ProtocolError('too many equipped records')
    body=bytearray(149)
    struct.pack_into('<QBBB',body,0,uid,slot,team,slot)
    body[11:32]=name.ljust(21,b'\0')
    body[53]=int(ready)
    body[54:57]=profile[122:125]
    body[64]=len(records)
    struct.pack_into('<I',body,67,p2p_id)
    body[76]=int(update)
    return bytes(body)+b''.join(records)


def room_entry_member(request, uid, room_id, slot, team, fighter, *, map_catalog=None):
    entry=bytearray(room_entry(request,uid,room_id,map_catalog=map_catalog))
    entry[10:12]=bytes((slot,team))
    entry[96:245]=fighter[:149]
    return Message(3100,bytes(entry))


def room_list_record(room_id, request, members, *, waiting=True, map_catalog=None):
    """9222D0 display fields,9253B0 join gates,824280 stride259."""
    if not 1<=room_id<=255:
        raise ProtocolError('native list byte-key room limit')
    config=room_entry(request,1,room_id,map_catalog=map_catalog)
    body=bytearray(259)
    struct.pack_into('<H',body,0,room_id)
    body[2:23]=config[24:45]
    body[23:31]=config[12:20]
    body[31]=config[61]  # Password lock; never publish the password.
    body[33]=config[57]
    body[34]=0  # Spectators not admitted by this local adapter yet.
    body[39:47]=bytes((config[62],members,int(waiting),config[59],config[60],config[65],0,0))
    return bytes(body)


def room_directory(records):
    if len(records)>9 or any(len(r)!=259 for r in records):
        raise ProtocolError('native nine-row page bound')
    return Message(2280,struct.pack('<II',1,len(records))+b''.join(records))


def battle_start_members(room_id, serial, local_slot, runtime_values):
    body=bytearray(battle_start(room_id,0,serial))
    struct.pack_into('<H',body,11,local_slot)
    for slot,value in runtime_values.items():
        if not 0<=slot<8:
            raise ProtocolError('battle slot bound')
        struct.pack_into('<I',body,13+slot*4,value)
    return Message(4080,bytes(body))
