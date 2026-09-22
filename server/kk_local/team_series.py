"""Native309-byte team-series codecs; not the ordinary500-byte result rows.

985A10 emits4111;8297D0/80A4A0 copy4112 into result4.sui. The local
result builder deliberately grants no currency and imports no guild data.
Admission/barriers are separate: recognizing this shape cannot start a series.
"""
from dataclasses import dataclass,field
import struct

from .menu_layouts import freeze_payload
from .wire import ProtocolError


@dataclass(frozen=True)
class SeriesRow:
    uid: int
    name_raw: bytes
    value_raw: int


@dataclass(frozen=True)
class SeriesReport:
    scores: tuple[int,int]
    perfect: bool
    organization_keys: tuple[int,int]
    organization_names_raw: tuple[bytes,bytes]
    rows: tuple[SeriesRow,...]


@dataclass
class SeriesProgress:
    """Local tracking of native Host decisions, not a second combat simulator."""
    limit: int
    scores: tuple[int,int]=(0,0)
    phase: str='playing'
    ready: set=field(default_factory=set)
    viewing: set=field(default_factory=set)
    versions: dict=field(default_factory=dict)
    report: SeriesReport | None=None

    def observe(self, decoded, owner, fighters, recipients=None):
        uid=decoded['sender'];ident=decoded['id']
        if (uid not in fighters or (decoded['flag'],decoded['header_variant'])!=(1,1)
                or ident not in (8294,8295,8296,8297)):return None
        sequence=decoded['sequence_19_raw']
        if sequence<=self.versions.get(uid,-1):return None
        if ident==8296:
            if (uid==owner or self.phase!='interval' or uid in self.ready or
                    (recipients is not None and owner not in recipients)):return None
            self.ready.add(uid);event='ready'
        elif uid!=owner:return None
        elif ident==8294:
            if self.phase!='playing':return None
            scores=decoded['interval_arguments_raw'];a,b=self.scores
            if scores not in ((a,b),(a+1,b),(a,b+1)) or max(scores)>self.limit//2:return None
            self.scores=scores;self.phase='interval';self.ready={owner};event='interval'
        elif ident==8295:
            if self.phase!='interval':return None
            #95B820 also permits native skip flags. Direct peer readiness may
            #not cross this service. The Host's actual8295 is the decision;
            #never manufacture it or wait for invented mandatory TCP votes.
            self.phase='playing';self.ready.clear();event='continue'
        else:
            if self.phase!='playing':return None
            self.phase='finishing';event='finish'
        self.versions[uid]=sequence
        return event

    def accepts_final(self, report):
        if self.phase!='finishing':return False
        a,b=self.scores;scores=report.scores;win=self.limit//2+1
        return (scores in ((a+1,b),(a,b+1)) and max(scores)==win and min(scores)<win
                and report.perfect==(min(scores)==0))


def decode_series_report(payload, expected_uids):
    """Read4111, binding every nonempty row to the actual fighter roster.

    The reporter is authenticated by the caller. value_raw is NOT a durable
    award: the outgoing result UI labels its corresponding field txtGold.
    """
    payload=freeze_payload(payload)
    if len(payload)!=309:raise ProtocolError('4111 exact309-byte length')
    expected=frozenset(expected_uids)
    if not expected or len(expected)>8 or any(type(u) is not int or not 0<u<2**64 for u in expected):
        raise ProtocolError('invalid series roster')
    if payload[2] not in (0,1):raise ProtocolError('series perfect flag')
    scores=struct.unpack_from('<ii',payload,3)
    if min(scores)<0:raise ProtocolError('negative series score')
    rows=[];seen=set()
    for i in range(8):
        raw=payload[61+31*i:92+31*i]
        uid=struct.unpack_from('<Q',raw)[0]
        if not uid:
            if any(raw):raise ProtocolError('nonempty vacant series row')
            continue
        if uid not in expected or uid in seen:raise ProtocolError('series row identity mismatch')
        name=raw[8:29]
        if b'\0' not in name:raise ProtocolError('unterminated series row name')
        seen.add(uid)
        rows.append(SeriesRow(uid,name,struct.unpack_from('<H',raw,29)[0]))
    if seen!=expected:raise ProtocolError('incomplete series roster')
    return SeriesReport(scores,bool(payload[2]),struct.unpack_from('<HH',payload,11),
                        (payload[19:40],payload[40:61]),tuple(rows))


def _name21(value):
    try:raw=value.encode('gbk')
    except (AttributeError,UnicodeEncodeError):raise ProtocolError('invalid series display name') from None
    if not raw or len(raw)>20 or b'\0' in raw:raise ProtocolError('series display name length')
    return raw.ljust(21,b'\0')


def no_award_series_result(report, names, teams, host_uid):
    """Build4112 public UI data from authenticated names, without DB changes.

    80AD60 anchors the first score to818880, which searches44B3F0=Host,
    NOT merely the first nonempty slot. It renders at most3 members per side.
    Guild icons/names and unclosed prefix fields stay absent/zero; input row
    values are never copied into the visible gold award.
    """
    uids={row.uid for row in report.rows}
    if set(names)!=uids or set(teams)!=uids or host_uid not in uids:
        raise ProtocolError('series result roster mismatch')
    if len(set(teams.values()))!=2:
        raise ProtocolError('series requires two teams')
    if any(sum(t==team for t in teams.values())>3 for team in set(teams.values())):
        raise ProtocolError('series result UI has three rows per side')
    if len(report.rows)>6 or len(uids)!=len(report.rows):raise ProtocolError('series result UI roster limit')
    if (len(report.scores)!=2 or any(type(x) is not int or not 0<=x<=0x7fffffff for x in report.scores)
            or type(report.perfect) is not bool):
        raise ProtocolError('invalid series result scores')
    payload=bytearray(309)
    payload[2]=int(report.perfect)
    struct.pack_into('<iiHH',payload,3,*report.scores,65535,65535)
    for i,row in enumerate(report.rows):
        struct.pack_into('<Q',payload,61+31*i,row.uid)
        payload[69+31*i:90+31*i]=_name21(names[row.uid])
        # row+29 signed16 is displayed by80A4E0 as txtGold. Local no-award0.
    return bytes(payload)
