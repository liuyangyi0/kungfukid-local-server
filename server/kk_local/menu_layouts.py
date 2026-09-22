"""Directional menu layouts from native consumers; no economic side effects.

Opaque regions retained for offline analysis are NOT safe for diagnostic logs.
Production telemetry must whitelist scalar fields, never log these dictionaries.
Decoding does not authorize a transaction or prove a server-side rule.
"""
import struct
from .wire import ProtocolError, MAX_FRAME
from .shop_catalog import ShopRecord, decode_catalog


def size(data, expected):
    if len(data) != expected:
        raise ProtocolError('menu layout length mismatch')


def u32(data, offset=0):
    return struct.unpack_from('<I', data, offset)[0]


def freeze_payload(data):
    """Own mutable buffers before retaining record slices; bound before copying."""
    if not isinstance(data,(bytes,bytearray,memoryview)):
        raise ProtocolError('menu payload must be bytes-like')
    length=data.nbytes if isinstance(data,memoryview) else len(data)
    if length>MAX_FRAME:
        raise ProtocolError('menu payload exceeds frame bound')
    return bytes(data)


def decode_menu_request(ident, data):
    data=freeze_payload(data)
    if ident == 2540:
        size(data,1)  # 88D5A0
        return dict(rank_category=data[0])
    if ident == 2560:
        size(data,9)  # 88C140; do not guess the identity word ordering.
        return dict(rank_category=data[0],opaque_identity=data[1:9])
    if ident == 1500:
        size(data,9)
        return dict(item_kind=data[0],item_id=u32(data,1),query_mode=u32(data,5))
    if ident == 9006:
        size(data,29)
        raw,sep,padding=data[8:].partition(b'\0')
        if not sep or not raw or any(padding):raise ProtocolError('invalid nickname text field')
        try:name=raw.decode('gbk')
        except UnicodeDecodeError as e:raise ProtocolError('invalid nickname encoding') from e
        return dict(actor=struct.unpack_from('<Q',data)[0],nickname=name)
    if ident == 2250:
        size(data,8)
        return dict(value_0=u32(data),selector=u32(data,4))
    if ident in (1320,1340,2171):
        size(data,12)
        return dict(actor=struct.unpack_from('<Q',data)[0],reference=u32(data,8))
    if ident == 1401:
        size(data,20)
        return dict(actor=struct.unpack_from('<Q',data)[0],reference=u32(data,8),
                    choice=u32(data,12),opaque_tail=data[16:20])
    if ident == 1402:
        size(data,16)
        return dict(actor=struct.unpack_from('<Q',data)[0],reference=u32(data,8),opaque_tail=data[12:16])
    if ident == 1440:
        size(data,4)
        return dict(reference=u32(data))
    if ident == 1420:
        size(data,173)
        return dict(reference=u32(data),purchase=decode_menu_request(9040,data[4:]))
    if ident in (9040, 9041, 9090, 9091):
        # 85D230/8406F0:169; 85C7E0/85BE90:426. No arbitrary body logging.
        size(data, 169 if ident in (9040, 9041) else 426)
        return dict(operation_code=u32(data), catalog_key=u32(data,145),
                    quoted_gold=u32(data,149), quoted_credit=u32(data,153),
                    quoted_ticket=u32(data,157), coupon_key=u32(data,161),
                    reference_key=u32(data,165))
    if ident == 9070:
        size(data,2)
        return dict(category=data[0],variant=data[1])
    if ident == 20561:
        size(data,4)
        return dict(page=u32(data))
    if ident == 6000:
        size(data,4)
        return dict(profile_selector=u32(data))
    if ident==21410:
        size(data,0)
        return {}
    if ident==21412:
        size(data,4)
        return dict(instance=u32(data))
    if ident in (6050,6080):
        size(data,14)
        #831400 does not initialize the first eight bytes. Neither the prefix
        #nor the process-local context authenticates an account.
        return dict(quest_id=struct.unpack_from('<H',data,12)[0],context=u32(data,8))
    if ident in (6051,6081,6311,6052,6082,6312):
        size(data,19)
        expected={6051:2,6052:2,6081:1,6082:1,6311:3,6312:3}[ident]
        if data[2]!=expected or not struct.unpack_from('<H',data)[0]:
            raise ProtocolError('quest action state/key mismatch')
        #The twelve-byte tail is uninitialized in the native sender. Do not
        #treat it as a receipt, nonce, reward, or another player's identity.
        return dict(quest_id=struct.unpack_from('<H',data)[0],state=data[2],context=u32(data,3))
    if ident in (1300,1400,1540,6001,6002,6003,6225,20563,20565,20546):
        size(data,0)
        return {}
    return None


def rewards(data):
    size(data,48)
    # Three 16-byte native tuples; type-specific interpretation is incomplete.
    return tuple(struct.unpack_from('<4I',data,i) for i in (0,16,32))


def decode_menu_response(ident, data):
    data=freeze_payload(data)
    if ident==21411:
        if not data or len(data)%21:raise ProtocolError('weapon level table stride')
        return dict(records=tuple(dict(level=u32(data,i),score_threshold=u32(data,i+4),gold=u32(data,i+8),
            odds=u32(data,i+12),unknown_16=data[i+16],bonus_percent=u32(data,i+17)) for i in range(0,len(data),21)))
    if ident==21413:
        if not data or len(data)%22:raise ProtocolError('weapon upgrade result stride')
        #A28E00 accepts multiples;861190 consumes the first record only.
        return dict(success=data[0]!=0,instance=u32(data,9),opaque_tail=data[22:])
    if ident==20547:
        size(data,4)
        return dict(profile_offset=352,value_u32=u32(data))
    if ident == 2550:
        if len(data)%27:raise ProtocolError('partial ranking record')
        records=[]
        for i in range(0,len(data),27):
            row=data[i:i+27]
            name,terminator,_=row[:21].partition(b'\0')
            if not terminator:raise ProtocolError('unterminated ranking name')
            try:nickname=name.decode('gbk')
            except UnicodeDecodeError as e:raise ProtocolError('invalid ranking name') from e
            records.append(dict(nickname=nickname,rank_zero_based=row[21],
                                score=struct.unpack_from('<i',row,22)[0],
                                rank_category=row[26],raw=row))
        return dict(records=tuple(records))
    if ident == 2570:
        # 88D250 consumes only this prefix. Do not assert a native total size.
        if len(data)<5:raise ProtocolError('short self ranking prefix')
        return dict(rank_category=data[0],rank_zero_based=struct.unpack_from('<i',data,1)[0],
                    opaque_tail=data[5:])
    if ident in (1510,1550):
        if len(data)%108:raise ProtocolError('partial quick-shop record')
        return dict(records=tuple(ShopRecord(data[i:i+108]) for i in range(0,len(data),108)))
    if ident == 2270:
        if len(data)<8 or (len(data)-8)%68:
            raise ProtocolError('player directory record alignment')
        return dict(header_0=u32(data),header_4=u32(data,4),
                    records=tuple(dict(identity=struct.unpack_from('<Q',data,i)[0],
                                       name_prefix=data[i+8:i+68].split(b'\0',1)[0],raw=data[i:i+68])
                                  for i in range(8,len(data),68)))
    if ident == 1230:
        if len(data)<4: raise ProtocolError('short account balance prefix')
        return dict(balance=u32(data),opaque_tail=data[4:])
    if ident in (2160,4121):
        size(data,68)
        return dict(instance=u32(data),item_id=u32(data,5),
                    quantity=struct.unpack_from('<H',data,23)[0],
                    quantity_mode='absolute',inventory_policy='upsert_instance',raw=data)
    if ident == 2161:
        if len(data)<68: raise ProtocolError('short inventory update')
        return dict(instance=u32(data),item_id=u32(data,5),
                    quantity=struct.unpack_from('<H',data,23)[0],quantity_mode='absolute',
                    inventory_policy='replace_existing_instance',raw=data[:68],opaque_tail=data[68:])
    if ident == 2162:
        size(data,4)
        return dict(instance=u32(data))
    if ident in (2120,2121):
        if len(data)<4 or len(data)!=4+4*u32(data):
            raise ProtocolError('inventory notification count mismatch')
        return dict(instances=struct.unpack_from('<'+'I'*u32(data),data,4))
    if ident == 1350:
        if not data or (data[0]!=0 and len(data)<5):
            raise ProtocolError('short information result')
        return dict(success=data[0]!=0,reference=u32(data,1) if data[0]!=0 else None,
                    opaque_tail=data[5:] if data[0]!=0 else data[1:])
    if ident in (1030,2040,2100,3030,4081):
        size(data,2)
        return dict(error_code=struct.unpack_from('<H',data)[0])
    if ident in (1240,1250):
        size(data,4)
        return dict(balance=struct.unpack_from('<i',data)[0])
    if ident in (1310,1410):
        stride=339 if ident==1310 else 124
        if len(data)%stride: raise ProtocolError('partial information record')
        return dict(records=tuple(data[i:i+stride] for i in range(0,len(data),stride)))
    if ident == 9080:
        category,variant,records=decode_catalog(data)
        return dict(category=category,variant=variant,records=records)
    if ident == 9050:
        # A2D600 exact108. This acknowledges UI purchase, not inventory insertion.
        return dict(record=ShopRecord(bytes(data)))
    if ident in (9060,9071,9110,6070,6100):
        if len(data)<2: raise ProtocolError('short native error')
        return dict(error_code=struct.unpack_from('<H',data)[0],opaque_tail=data[2:])
    if ident == 20562:
        size(data,878)  # A2BD10; 68 + 10*33 + 10*48
        page=u32(data)
        if page>10: raise ProtocolError('page outside native cache admission')
        entries=[]
        for i in range(10):
            p=68+33*i
            entries.append(dict(presence_key=u32(data,p),consume=u32(data,p+8),
                                name_bytes=data[p+12:p+33],
                                rewards=rewards(data[398+48*i:446+48*i])))
        return dict(page=page,opaque_header=data[4:68],entries=tuple(entries))
    if ident == 20564:
        size(data,68)
        return dict(rank_zero_based=struct.unpack_from('<i',data)[0],
                    opaque_identity=data[4:16],consume=u32(data,16),
                    rewards=rewards(data[20:68]))
    if ident == 20566:
        return dict(rewards=rewards(data))
    if ident == 6226:
        if len(data)%4: raise ProtocolError('partial quest ID')
        return dict(quest_ids=struct.unpack('<'+'I'*(len(data)//4),data))
    if ident == 6020:
        #821BD0 requires >=7 even when floor(len/123)==0, then refreshes UI.
        #Our encoder uses one canonical zero sentinel; arbitrary tails remain
        #rejected rather than copying the native partial-record tolerance.
        if data==bytes(7):return dict(records=())
        if not data or len(data)%123: raise ProtocolError('partial quest record')
        return dict(records=tuple(dict(quest_id=struct.unpack_from('<H',data,i+4)[0],
                                       raw=data[i:i+123]) for i in range(0,len(data),123)))
    if ident in (6031,6061,6091,6301,6032,6062,6092,6302,6033,6063,6093,6303):
        # Only the consumed prefix is proven, not the total packet length.
        if len(data)<3: raise ProtocolError('short quest state prefix')
        return dict(quest_id=struct.unpack_from('<H',data)[0],state=data[2],
                    lookup_bias=4000 if ident%10==3 else 0,opaque_tail=data[3:])
    if ident in (6041,6042,6043):
        if len(data)%141: raise ProtocolError('partial extended quest record')
        return dict(records=tuple(dict(quest_id=struct.unpack_from('<H',data,i+12)[0],
                                       lookup_bias=4000 if ident==6043 else 0,
                                       raw=data[i:i+141]) for i in range(0,len(data),141)))
    if ident == 6223:
        # Success reads +5 u32; failure reads +1 u32. Total length not proven.
        if not data or len(data)<(9 if data[0]==1 else 5):
            raise ProtocolError('short quest action result')
        return dict(success=data[0]==1,error_code=None if data[0]==1 else u32(data,1),
                    quest_id=u32(data,5) if data[0]==1 else None,
                    opaque_tail=data[9:] if data[0]==1 else data[5:])
    if ident in (6030,6060,6090):
        if len(data)<14: raise ProtocolError('short quest notification')
        return dict(quest_id=struct.unpack_from('<H',data,12)[0],
                    opaque_prefix=data[:12],opaque_tail=data[14:])
    if ident == 6040:
        # Both observed tables consume this prefix; their UI side effects differ.
        if len(data)<6: raise ProtocolError('short quest completion prefix')
        return dict(quest_id=struct.unpack_from('<H',data,4)[0],
                    opaque_prefix=data[:4],opaque_tail=data[6:])
    return None
