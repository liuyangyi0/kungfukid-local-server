"""Known field decoders, NOT authority to apply these messages.

Source: docs/protocol/gfxz-client-protocol-with-battle.md sections7–20.
Uninterpreted fields remain opaque. Unknown enums remain unknown.
"""
import math
import struct
from .wire import ProtocolError
from .menu_layouts import decode_menu_request

BATTLE_LENGTHS = {0x1fb8:108, 0x1fcc:103, 0x1fd6:87, 0x2062:75,
                  0x20ca:64, 0x20cd:64, 0x20f8:71, 0x20f9:47,
                  0x2102:59, 0x2103:47}


def decode_consumption(data):
    """8289 owned-consumable event; not the unrelated8290 paid-use path."""
    exact(data, 75)
    ident, sender = struct.unpack_from('<IQ',data)
    if ident != 8289 or data[12:14] != b'\x01\x01':
        raise ProtocolError('invalid consumption header')
    seq, elapsed = struct.unpack_from('<II',data,19)
    slot = struct.unpack_from('<I',data,39)[0]
    target = struct.unpack_from('<Q',data,59)[0]
    room, serial = struct.unpack_from('<II',data,67)
    if slot not in (27,28):
        raise ProtocolError('unsupported consumption slot')
    return dict(sender=sender,target=target,seq=seq,elapsed=elapsed,
                slot=slot,room=room,serial=serial,
                signature=struct.pack('<I',elapsed)+data[39:])


def exact(data, size):
    if len(data) != size:
        raise ProtocolError('known-layout length mismatch')


def finite(values):
    if not all(math.isfinite(v) for v in values):
        raise ProtocolError('nonfinite battle value')
    return values


def decode_battle(data):
    if len(data) < 39:
        raise ProtocolError('short battle header')
    ident, sender = struct.unpack_from('<IQ', data)
    if ident not in BATTLE_LENGTHS:
        return None
    exact(data, BATTLE_LENGTHS[ident])
    result = dict(id=ident, sender=sender, flag=data[12], opaque_header=data[13:23],
                  elapsed_time_ms=struct.unpack_from('<I', data, 23)[0],
                  opaque_header_tail=data[27:31],
                  header_args=struct.unpack_from('<ii', data, 31))
    if ident == 0x1fb8:
        result.update(position=finite(struct.unpack_from('<fff', data, 51)),
                      facing=finite(struct.unpack_from('<fff', data, 63)),
                      target=struct.unpack_from('<Q', data, 75)[0],
                      skill_time=finite(struct.unpack_from('<ff', data, 87)),
                      state=struct.unpack_from('<H', data, 97)[0],
                      animation=struct.unpack_from('<I', data, 99)[0],
                      direction=data[103], scene=data[104], force_direction=data[105])
    elif ident == 0x1fcc:
        result.update(attacker=struct.unpack_from('<Q', data, 39)[0],
                      type_subtype_target_context=struct.unpack_from('<IIII', data, 47),
                      position=finite(struct.unpack_from('<fff', data, 63)),
                      values=struct.unpack_from('<IIIII', data, 75),
                      room_pair=struct.unpack_from('<II', data, 95))
    elif ident == 0x1fd6:
        result.update(player=struct.unpack_from('<Q', data, 39)[0],
                      source=struct.unpack_from('<Q', data, 47)[0],
                      code_parameter_event=struct.unpack_from('<III', data, 55),
                      action=struct.unpack_from('<I', data, 75)[0],
                      room_pair=struct.unpack_from('<II', data, 79))
    elif ident == 0x2062:
        result.update(weapon_kind=struct.unpack_from('<I', data, 51)[0],
                      player=struct.unpack_from('<Q', data, 59)[0],
                      room_pair=struct.unpack_from('<II', data, 67))
    elif ident == 0x20ca:
        result.update(winner=struct.unpack_from('<Q', data, 39)[0],
                      loser=struct.unpack_from('<Q', data, 47)[0],
                      teams=struct.unpack_from('<ii', data, 55), latched=data[63])
    else:
        result['opaque_body'] = data[39:]  # Equal length does not imply equal semantics.
    return result


def decode_known(ident, data):
    menu = decode_menu_request(ident, data)
    if menu is not None:
        return menu
    if ident in (0x03f2, 0x07da):
        exact(data, 96)
        return dict(identity=struct.unpack_from('<Q', data)[0],
                    context=struct.unpack_from('<I', data, 8)[0],
                    build=struct.unpack_from('<I', data, 49)[0])
    if ident == 0x0820:
        exact(data, 16)
        instance, slot = struct.unpack_from('<II', data)
        return dict(instance=instance, slot=slot, opaque=data[8:])
    if ident == 0x047e:
        exact(data, 68)
        try:
            nickname = data[:21].split(b'\0', 1)[0].decode('gbk')
        except UnicodeDecodeError as exc:
            raise ProtocolError('invalid nickname encoding') from exc
        return dict(nickname=nickname,
                    character_type=data[21], variant=data[22],
                    choices=struct.unpack_from('<7I', data, 23), opaque=data[51:59],
                    client_kind=data[59], promoter=struct.unpack_from('<Q', data, 60)[0])
    if ident == 0x0848:
        if len(data) < 4:
            raise ProtocolError('short item-status list')
        n = struct.unpack_from('<i', data)[0]
        if n < 0 or len(data) != 4 + n * 4:
            raise ProtocolError('item-status count mismatch')
        return dict(instances=struct.unpack_from(f'<{n}I', data, 4))
    if ident == 0x1f87:
        return decode_battle(data)
    return None
