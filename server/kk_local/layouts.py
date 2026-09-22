"""Known field decoders, NOT authority to apply these messages.

Source: docs/protocol/gfxz-client-protocol-with-battle.md, including sections46–52.
Uninterpreted fields remain opaque. Unknown enums remain unknown.
"""
import math
import struct
from .wire import ProtocolError
from .menu_layouts import decode_menu_request, freeze_payload

BATTLE_LENGTHS = {0x1fb8:108, 0x1fb9:94, 8126:71, 0x1fcc:103, 0x1fd6:87, 0x2062:75,
                  8122:51, 8125:53, 8142:59, 8276:51, 8278:55, 8286:55, 8293:51,
                  8127:63, 8143:59, 8144:115, 8270:87, 8280:55, 8282:47, 8284:75,
                  8155:334, 8157:55, 8287:63, 8288:55,
                  8294:47, 8295:39, 8296:43, 8297:39,
                  8400:131,8401:123,8402:123,8403:119,8404:51,
                  9000:63,9001:63,9002:63,
                  9500:55,9501:55,9502:55,
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


def matrix_payload(data,offset):
    # RW-compatible64-byte frame: four Vec3 lanes with intervening raw words.
    # Preserve flags/padding bits, not sixteen floating-point scalars.
    vectors=tuple(finite(struct.unpack_from('<fff',data,offset+i)) for i in (0,16,32,48))
    return dict(transform_raw=data[offset:offset+64],transform_vectors=vectors,
                transform_words=tuple(struct.unpack_from('<I',data,offset+i)[0] for i in (12,28,44,60)))


def decode_battle(data):
    # Own mutable buffers before returning opaque slices or raw field views.
    # Known fixed-shape messages keep this service's strict length policy;
    # native8150/8121 consumers accept a minimum prefix, not exact equality.
    data=freeze_payload(data)
    length=len(data)
    if length < 39:
        raise ProtocolError('short battle header')
    ident, sender = struct.unpack_from('<IQ', data)
    if ident not in BATTLE_LENGTHS:
        return None
    if length != BATTLE_LENGTHS[ident]:
        raise ProtocolError('known-layout length mismatch')
    exact(data, BATTLE_LENGTHS[ident])
    result = dict(id=ident, sender=sender, flag=data[12], opaque_header=data[13:23],
                  elapsed_time_ms=struct.unpack_from('<I', data, 23)[0],
                  opaque_header_tail=data[27:31],
                  header_args=struct.unpack_from('<ii', data, 31))
    result.update(header_variant=data[13],sequence_15_raw=struct.unpack_from('<I',data,15)[0],
                  sequence_19_raw=struct.unpack_from('<I',data,19)[0])
    if ident == 0x1fb8:
        result.update(position=finite(struct.unpack_from('<fff', data, 51)),
                      facing=finite(struct.unpack_from('<fff', data, 63)),
                      target=struct.unpack_from('<Q', data, 75)[0],
                      skill_time=finite(struct.unpack_from('<ff', data, 87)),
                      state=struct.unpack_from('<H', data, 97)[0],
                      animation=struct.unpack_from('<I', data, 99)[0],
                      direction=data[103], scene=data[104], force_direction=data[105])
    elif ident == 0x1fb9:
        #82A9D0: +67>0 damages; otherwise heals by its negation. Sources can
        # be absent. None of these decoded values authorize HP/MP mutation.
        amount,target_mp,source_mp,scalar80=finite((struct.unpack_from('<f',data,67)[0],
            *struct.unpack_from('<fff',data,72)))
        result.update(target=struct.unpack_from('<Q',data,39)[0],
                      source=struct.unpack_from('<Q',data,47)[0],
                      metadata_55=data[55],skill_property_id=struct.unpack_from('<I',data,56)[0],
                      opaque_60_65=data[60:65],attack_callback_flag=data[65],
                      damage_argument_66=data[66],signed_hp_amount=amount,
                      signed_hp_amount_bits=struct.unpack_from('<I',data,67)[0],
                      contribution_flag=data[71],target_mp_argument=target_mp,
                      source_mp_argument=source_mp,scalar_80=scalar80,
                      callback_mode=data[84],hit_result_status=data[85],
                      room_pair=struct.unpack_from('<II',data,86))
    elif ident == 8126:
        #82B8D0 may produce8150 from SkillProperty.attacker_ustates after a
        # local-session gate. Decode identities without authorizing that replay.
        result.update(reporter=struct.unpack_from('<Q',data,39)[0],
                      target=struct.unpack_from('<Q',data,47)[0],
                      source=struct.unpack_from('<Q',data,55)[0],
                      skill_property_id=struct.unpack_from('<I',data,63)[0],
                      hit_result_status_raw=struct.unpack_from('<I',data,67)[0])
    elif ident == 8122:
        #82A8B0: resolve player then set direction; no room pair in this body.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      direction_raw=struct.unpack_from('<I',data,47)[0])
    elif ident == 8125:
        #828120: current action must match before consuming the u16 argument.
        # Its vcall meaning is not inferred from the width or neighboring IDs.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      action_id=struct.unpack_from('<I',data,47)[0],
                      action_argument_51=struct.unpack_from('<H',data,51)[0])
    elif ident == 8142:
        #8287A0: exact trap key; middle bytes are not consumed here.
        result.update(object_key=struct.unpack_from('<I',data,39)[0],
                      opaque_43_51=data[43:51],
                      room_pair=struct.unpack_from('<II',data,51))
    elif ident == 8127:
        #827D00 ->9E4470 replaces current_mp_038, not an MP delta.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      opaque_47_51=data[47:51],
                      current_mp=finite(struct.unpack_from('<f',data,51))[0],
                      current_mp_bits=struct.unpack_from('<I',data,51)[0],
                      room_pair=struct.unpack_from('<II',data,55))
    elif ident == 8143:
        #8283D0 ->9E3C10; 9F5EC0 producer writes a target UID and timeout.
        # The three DWORD arguments are NOT a Vec3 or a room pair.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      target=struct.unpack_from('<Q',data,47)[0],
                      target_timeout_raw=struct.unpack_from('<I',data,55)[0])
    elif ident == 8144:
        #82B840 ->9F5160 consumes both actors and both transforms. The native
        # consumer applies second position/facing before first position/facing,
        # then requests first state and second state; this is only decoding.
        result.update(first_player=struct.unpack_from('<Q',data,39)[0],
                      second_player=struct.unpack_from('<Q',data,47)[0],
                      first_position=finite(struct.unpack_from('<fff',data,55)),
                      first_facing=finite(struct.unpack_from('<fff',data,67)),
                      first_state_raw=struct.unpack_from('<I',data,79)[0],
                      second_position=finite(struct.unpack_from('<fff',data,83)),
                      second_facing=finite(struct.unpack_from('<fff',data,95)),
                      second_state_raw=struct.unpack_from('<I',data,107)[0],
                      second_skill_property_id=struct.unpack_from('<I',data,111)[0])
    elif ident == 8270:
        #828BF0: independently arms slip for each actor which exists.
        result.update(first_player=struct.unpack_from('<Q',data,39)[0],
                      second_player=struct.unpack_from('<Q',data,47)[0],
                      first_direction=finite(struct.unpack_from('<fff',data,55)),
                      second_direction=finite(struct.unpack_from('<fff',data,67)),
                      first_distance=finite(struct.unpack_from('<f',data,79))[0],
                      second_distance=finite(struct.unpack_from('<f',data,83))[0])
    elif ident == 8280:
        #8281D0 branches differently when target lookup fails; zero is not
        # silently rewritten to self and decode does not invoke the action.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      target=struct.unpack_from('<Q',data,47)[0])
    elif ident == 8282:
        #82A030 resolves an object key; remaining4 bytes stay uninterpreted.
        result.update(object_key=struct.unpack_from('<I',data,39)[0],
                      opaque_43_47=data[43:47])
    elif ident == 8284:
        #82B010 ->9F4420 ->weapon callback: keep unresolved virtual payload.
        result.update(weapon_operation_raw=struct.unpack_from('<I',data,39)[0],
                      operation_argument_43_raw=struct.unpack_from('<I',data,43)[0],
                      opaque_47_59=data[47:59],
                      player=struct.unpack_from('<Q',data,59)[0],
                      room_pair=struct.unpack_from('<II',data,67))
    elif ident == 8276:
        #828CF0: object lookup and A527C0 operation after time/room gates.
        result.update(object_key=struct.unpack_from('<I',data,39)[0],
                      room_pair=struct.unpack_from('<II',data,43))
    elif ident == 8155:
        #9884D0/82C1D0: fixed7-byte prefix +8*36-byte rows, NOT a count.
        rows=[]
        for slot in range(8):
            offset=46+36*slot
            rows.append(dict(slot=slot,player=struct.unpack_from('<Q',data,offset)[0],
                field_08_raw=struct.unpack_from('<I',data,offset+8)[0],
                field_0c_raw=struct.unpack_from('<I',data,offset+12)[0],
                field_10_raw=struct.unpack_from('<H',data,offset+16)[0],
                field_12_raw=struct.unpack_from('<H',data,offset+18)[0],
                fields_14_1c_raw=struct.unpack_from('<III',data,offset+20),
                counter_20_raw=struct.unpack_from('<H',data,offset+32)[0],
                opaque_22_24=data[offset+34:offset+36]))
        result.update(mode_prefix=data[39:46],rows=tuple(rows))
    elif ident == 8157:
        #964DD0 Host-only producer, mode16 receiver virtual+160. No invented
        # event enum, variable record count, or automatic event application.
        result.update(participant=struct.unpack_from('<Q',data,39)[0],
                      event_code_raw=struct.unpack_from('<I',data,47)[0],
                      value_raw=struct.unpack_from('<I',data,51)[0])
    elif ident == 8287:
        #8298D0 selects gem(kind1/2) or coin(kind3) manager and spawns.
        kind=struct.unpack_from('<I',data,39)[0]
        result.update(spawn_kind_raw=kind,spawn_family={1:'gem',2:'gem',3:'coin'}.get(kind,'unknown'),
                      object_id=struct.unpack_from('<I',data,43)[0],
                      spawn_region_index=struct.unpack_from('<I',data,47)[0],
                      position=finite(struct.unpack_from('<fff',data,51)))
    elif ident == 8288:
        #828050 only consumes actor identity, conditional on action1013.
        result.update(player=struct.unpack_from('<Q',data,39)[0],opaque_47_55=data[47:55])
    elif ident == 8294:
        #82D090 ->95C7E0 ->95B880 UI interval arguments; enums not closed.
        result.update(interval_arguments_raw=struct.unpack_from('<II',data,39))
    elif ident == 8296:
        #82AC30 uses common sender as actor, gated by local player's Host.
        result.update(value_39_raw=struct.unpack_from('<I',data,39)[0])
    elif ident == 8278:
        #828EB0: owner-only UI path. +47 is not proven to be another ID.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      opaque_47_51=data[47:51],
                      ui_value_51_raw=struct.unpack_from('<I',data,51)[0])
    elif ident == 8286:
        #8285E0: target ready flag only if initiator==target or initiator Host.
        # The parser must not grant that authority merely from this identity.
        result.update(initiator=struct.unpack_from('<Q',data,39)[0],
                      target=struct.unpack_from('<Q',data,47)[0])
    elif ident == 8293:
        #82AD60: nonlocal target passes raw +47 to9F3C00; keep enum unknown.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      value_47_raw=struct.unpack_from('<I',data,47)[0])
    elif ident in (9000,9001,9002):
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      pickup_key=struct.unpack_from('<I',data,47)[0],
                      pickup_value_raw=struct.unpack_from('<I',data,51)[0],
                      room_pair=struct.unpack_from('<II',data,55))
    elif ident in (9500,9501,9502):
        # World-chest reservation has the same request triple, NO room pair.
        result.update(player=struct.unpack_from('<Q',data,39)[0],
                      pickup_key=struct.unpack_from('<I',data,47)[0],
                      pickup_value_raw=struct.unpack_from('<I',data,51)[0])
    elif ident == 8400:
        result.update(source=struct.unpack_from('<Q',data,39)[0],
                      child_key=struct.unpack_from('<I',data,47)[0],
                      definition_id=struct.unpack_from('<I',data,51)[0],
                      scene_id_raw=struct.unpack_from('<I',data,119)[0],
                      room_pair=struct.unpack_from('<II',data,123),**matrix_payload(data,55))
    elif ident in (8401,8402,8403):
        result['child_key']=struct.unpack_from('<I',data,39)[0]
        if ident==8402:
            result['target']=struct.unpack_from('<Q',data,43)[0]
            result.update(matrix_payload(data,51))
        else:
            result['result_state_raw']=struct.unpack_from('<I',data,43)[0]
            result.update(matrix_payload(data,47))
            if ident==8401:result['opaque_111_115']=data[111:115]
        result['room_pair']=struct.unpack_from('<II',data,111 if ident==8403 else 115)
    elif ident==8404:
        result.update(child_key=struct.unpack_from('<I',data,39)[0],
                      room_pair=struct.unpack_from('<II',data,43))
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
        result.update(target=result['player'],ustate_code=result['code_parameter_event'][0],
                      parameter_signed=struct.unpack_from('<i',data,59)[0],
                      event_raw=result['code_parameter_event'][2],opaque_67_75=data[67:75],
                      operation={0:'cancel',1:'apply'}.get(result['action'],'unknown'))
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
