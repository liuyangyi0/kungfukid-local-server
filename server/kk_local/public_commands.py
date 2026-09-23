"""Public exposure policy over existing parsers/handlers, not new gameplay.

Unknown and unreviewed families are never forwarded. Owned-control relay is
not a proof that reported motion, damage or outcomes are truthful.
"""
import struct
from .wire import ProtocolError,GameDecoder
from .layouts import decode_battle
from .public_policy import FairRate

# Only exposure, length envelope and phase live here. Field semantics/ownership
# remain in the existing handlers. This is not a second protocol catalogue.
SPECS={
    0:(0,0,'all'),1010:(96,96,'connected'),2010:(96,96,'connected'),
    1157:(0,0,'bootstrap profile_sent handoff lobby'),3320:(4,4,'profile_sent handoff'),
    2060:(0,0,'bootstrap profile_sent lobby'),1156:(12,12,'lobby room'),2250:(8,8,'lobby room'),2260:(3,3,'lobby room'),
    3010:(81,81,'lobby room'),3070:(14,14,'lobby room'),3075:(1,1,'lobby'),3110:(0,0,'lobby room loading wait_ready battle'),
    3200:(48,48,'room'),3230:(1,1,'room'),3140:(9,9,'room'),4051:(12,12,'room'),4031:(0,0,'room'),
    3500:(39,39,'room'),3501:(18,18,'lobby'),3502:(37,37,'lobby'),4200:(4,4,'battle'),
    4030:(0,0,'room'),4060:(0,0,'room'),4160:(0,0,'loading wait_ready'),8040:(14,14,'wait_ready battle'),
    4151:(0,0,'room'),3550:(12,12,'room battle'),4110:(0,8192,'room battle'),4115:(4,4,'room battle'),
    8071:(39,334,'battle'),4082:(0,0,'battle'),2080:(16,16,'lobby room'),2300:(4,4,'lobby room'),2110:(0,0,'lobby room'),
    9070:(2,2,'lobby room'),1540:(0,0,'lobby room'),1500:(0,256,'lobby room'),
    9040:(169,169,'lobby room'),9041:(169,169,'lobby room'),9090:(0,1024,'lobby room'),9091:(0,1024,'lobby room'),
    1300:(0,1024,'lobby room'),1320:(0,1024,'lobby room'),1340:(0,1024,'lobby room'),2171:(0,1024,'lobby room'),
    1400:(0,1024,'lobby room'),1420:(0,1024,'lobby room'),1440:(0,1024,'lobby room'),
    4202:(0,1024,'lobby room'),4204:(0,1024,'lobby room'),21410:(0,1024,'lobby room'),21412:(0,1024,'lobby room'),
    9006:(0,128,'lobby'),5002:(215,215,'lobby room battle'),5000:(256,256,'lobby room battle'),
    2420:(8,8,'lobby room'),20360:(12,12,'lobby room'),20546:(0,0,'lobby room')}
# Exposure is only the first gate. Shared RoomHub handlers enforce correlated
# hit receipts, owned states, paired transforms, projectile and Host lifetimes.
BASIC_BATTLE=frozenset((8120,8121,8122,8125,8126,8127,8140,8143,8144,8150,8270,8278,
                        8280,8284,8286,8287,8288,8293,8400,8401,8402,8403,8404,
                        9000,9001,9002,9500,9501,9502))
DATABASE_COMMANDS=frozenset((1010,2010,2250,2260,3010,3070,3200,3230,3140,4030,4060,4160,8040,4110,
    2080,2110,2300,9070,1540,1500,9040,9090,1300,1320,1340,2171,1400,1420,1440,4202,4204,21410,21412,9006,2420,20360,20546,4200,4082))

def validate_header(ident,size):
    low,high,_=SPECS.get(ident,(0,1024,''))
    if not low<=size<=high:raise ProtocolError('public command length')

def validate_battle(engine,payload):
    room=engine.room;uid=engine.account_uid;decoded=decode_battle(payload)
    if room is None or room.stage!='battle' or uid not in room.fighters or decoded is None:raise ProtocolError('public battle context')
    if decoded['id'] not in BASIC_BATTLE:raise ProtocolError('public battle family unavailable')
    if engine.game is None or engine.game.uid!=uid or room.members[uid].engine is not engine:raise ProtocolError('public battle connection')
    if decoded['sender']!=uid:raise ProtocolError('public actor spoof')
    # Family-specific authority is enforced by the SAME RoomHub handlers for
    # both transports; e.g.8144 target-owned pair,8126 correlated source reply,
    # Host death/collectibles and registered8150 cancellation are not self-only.
    if decoded['id'] in (8122,8125,8127,8143,8280,8284,8288,8293) and decoded['player']!=uid:raise ProtocolError('public actor spoof')
    if decoded['id']==8121 and decoded['target']!=uid:raise ProtocolError('public hit target spoof')
    for key in (() if decoded['id'] in (8400,8401,8402,8403,8404) else ('source','target')):
        if key in decoded and decoded[key] not in (0,*room.fighters):raise ProtocolError('public battle reference outside room')
    if 'room_pair' in decoded and decoded['room_pair']!=(room.number,room.serial):raise ProtocolError('public stale battle')
    if (decoded['flag'],decoded['header_variant'])!=((0,0) if decoded['id']==8120 else (1,1)):raise ProtocolError('public battle header')
    return decoded

class PublicCommands:
    def __init__(self,policy):
        self.policy=policy;self.rate=FairRate(policy.messages_per_second,policy.messages_per_second*2,limit=1)
        self.bytes=FairRate(policy.bytes_per_second,policy.bytes_per_second*2,limit=1)
        self.expensive=FairRate(policy.expensive_rate,policy.expensive_burst,limit=1);self.unknown=FairRate(policy.unknown_rate,policy.unknown_burst,limit=1)
        self.database=FairRate(policy.database_rate,policy.database_burst,limit=1)
    def before(self,e,c,m):
        validate_header(m.id,len(m.payload))
        if not self.rate.take(0) or not self.bytes.take(0,len(m.payload)+24):raise ProtocolError('public command rate')
        spec=SPECS.get(m.id)
        if spec is None:
            if not self.unknown.take(0):raise ProtocolError('unsupported command flood')
            return False
        if spec[2]!='all' and c.phase.value not in spec[2].split():raise ProtocolError('public command phase')
        if m.id not in (0,1010,2010) and c.uid!=e.account_uid:raise ProtocolError('public connection identity')
        if m.id in DATABASE_COMMANDS and not self.database.take(0):raise ProtocolError('public database operation rate')
        if m.id==3010 and m.payload[46] not in (0,1,2,3,5):raise ProtocolError('public mode unavailable')
        if m.id==8071:
            if struct.unpack_from('<I',m.payload)[0]==8289:
                if not self.database.take(0):raise ProtocolError('public database operation rate')
                from .layouts import decode_consumption
                event=decode_consumption(m.payload)
                if not e.room or event['sender']!=c.uid or event['target']!=c.uid or (event['room'],event['serial'])!=(e.room.number,e.room.serial):raise ProtocolError('public consume identity')
            else:validate_battle(e,m.payload)
        if m.id in (1500,1540,2110,9070,2080,2300,9040,9090,4204,21412,2420) and not self.expensive.take(0):raise ProtocolError('public expensive operation rate')
        return True
    def udp(self,e,payload):
        decoder=GameDecoder(header_validator=validate_header);messages=decoder.feed(payload);decoder.eof()
        if not 1<=len(messages)<=8:raise ProtocolError('public UDP frame count')
        if not self.rate.take(0,len(messages)) or not self.bytes.take(0,len(payload)):raise ProtocolError('public battle work budget')
        for message in messages:
            if message.id!=8071:raise ProtocolError('public UDP direction')
            validate_battle(e,message.payload)
        return messages
