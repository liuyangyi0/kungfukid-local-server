"""Mode0–3 no-award result policy (SYSTEM_DESIGN_INFERRED / PROVISIONAL).

4110/87-byte slots and 4120/500-byte rows follow the native consumers recorded
in unified-local-service-multiplayer-20260912-2201/reward-consumers.json.
Reports are untrusted observations, never instructions to mutate the database.
"""
from dataclasses import dataclass
import struct

from .wire import ProtocolError


@dataclass(frozen=True)
class ReportSlot:
    uid: int
    maximum_hp: int
    hp: int
    result_parameter: int
    raw: bytes

    @property
    def enemy_kills_low8(self):
        return self.raw[8]  #987F75 truncates native statistics+8 to one byte.

    @property
    def penalty_kills_low8(self):
        return self.raw[9]  #987F.. copies statistics+10, not victim deaths.

    @property
    def deaths_u16(self):
        return struct.unpack_from('<H',self.raw,15)[0]

    @property
    def reborn_points(self):
        return struct.unpack_from('<i',self.raw,37)[0]  #987E40 copies CPlayer+018.

    @property
    def reborn_counts(self):
        return struct.unpack_from('<HH',self.raw,45)  #mode tracking row+10/+12.


def decode_report(payload, roster, room_number, serial):
    """Validate all eight slots against the current authoritative room roster."""
    if len(payload) != 696:
        raise ProtocolError('4110 requires eight 87-byte slots')
    payload = bytes(payload)
    result = {}
    for slot in range(8):
        raw = payload[slot*87:(slot+1)*87]
        uid = struct.unpack_from('<Q', raw, 29)[0]
        expected = roster.get(slot)
        if expected is None:
            if any(raw):
                raise ProtocolError('4110 nonempty unoccupied slot')
            continue
        if uid != expected or uid in result:
            raise ProtocolError('4110 roster identity mismatch')
        if struct.unpack_from('<II', raw, 67) != (room_number, serial):
            raise ProtocolError('4110 stale room or battle')
        maximum, hp = struct.unpack_from('<HH', raw)
        if not maximum or hp > maximum:
            raise ProtocolError('4110 HP bounds')
        result[uid] = ReportSlot(uid, maximum, hp, struct.unpack_from('<H', raw, 65)[0], raw)
    if not result:
        raise ProtocolError('4110 empty roster')
    return result


def consensus_survival(reports, teams):
    """Local policy only: all peers must agree on all remaining HP.

One surviving team wins; otherwise show a draw. No HP sum/time tie-break,
original-server score formula, currency, experience or win-counter update.
The opaque native result_parameter is deliberately NOT treated as a winner.
"""
    if set(reports) != set(teams):
        return None
    observations = list(reports.values())
    baseline = observations[0]
    if set(baseline) != set(teams):
        raise ProtocolError('settlement roster mismatch')
    for report in observations[1:]:
        if (set(report) != set(baseline) or any(
                (report[u].maximum_hp, report[u].hp) != (baseline[u].maximum_hp, baseline[u].hp)
                for u in baseline)):
            raise ProtocolError('settlement peer HP disagreement')
    alive = {teams[u] for u, value in baseline.items() if value.hp > 0}
    return {u: (1 if teams[u] in alive else 2) if len(alive) == 1 else 0 for u in teams}


def consensus_elimination(reports, groups, *, team_mode):
    """Local no-award ranking of agreed wire counters, NOT old-server scoring.

    Individual: enemy minus self/friendly kills. Two teams: own enemy kills
    plus opposing self/friendly kills, following the native completion inputs.
    Ties are draws. The wire exposes only low8 counters; no invented high bits.
    """
    if set(reports)!=set(groups):return None
    if not groups:raise ProtocolError('settlement empty roster')
    baseline=next(iter(reports.values()))
    if set(baseline)!=set(groups):raise ProtocolError('settlement roster mismatch')
    for report in reports.values():
        if set(report)!=set(groups) or any(
            (report[u].enemy_kills_low8,report[u].penalty_kills_low8)!=
            (baseline[u].enemy_kills_low8,baseline[u].penalty_kills_low8) for u in groups):
            raise ProtocolError('settlement peer score disagreement')
    if team_mode:
        teams=set(groups.values())
        if len(teams)!=2:raise ProtocolError('team elimination needs two teams')
        scores={team:sum(r.enemy_kills_low8 if groups[u]==team else r.penalty_kills_low8
                         for u,r in baseline.items()) for team in teams}
    else:
        scores={u:r.enemy_kills_low8-r.penalty_kills_low8 for u,r in baseline.items()}
        groups={u:u for u in groups}
    highest=max(scores.values())
    winners={key for key,value in scores.items() if value==highest}
    return {u:(1 if groups[u] in winners else 2) if len(winners)==1 else 0 for u in groups}


def consensus_reborn(reports, participants):
    """Local Mode16 policy: highest agreed native points; top ties are draws.

    Mode16 points come from CPlayer+018, not HP or truncated ordinary kill
    counters. All fighter reports must agree on points and the mode counters.
    This is not a recovered old-server ranking or economy formula.
    """
    participants=set(participants)
    if set(reports)!=participants:return None
    baseline=next(iter(reports.values()))
    if set(baseline)!=participants:raise ProtocolError('reborn settlement roster mismatch')
    for report in reports.values():
        if set(report)!=participants or any(
                (report[u].reborn_points,report[u].reborn_counts)!=
                (baseline[u].reborn_points,baseline[u].reborn_counts) for u in participants):
            raise ProtocolError('reborn peer points/count disagreement')
    highest=max(r.reborn_points for r in baseline.values())
    winners={u for u,r in baseline.items() if r.reborn_points==highest}
    return {u:(1 if u in winners else 2) if len(winners)==1 else 0 for u in sorted(participants)}


def result_payload(results, recipient, profile, *, point_awards=None, observer=False, reborn_reports=None):
    """4120 rows: UID+0, draw/win/loss+10,140-byte UI prefix,360-byte profile.

Only the receiving player's row carries its unchanged authoritative profile.
Peer tails are absent (ID0), never another account's profile with matching
role ID: native9CD2F0 uses role ID, not the outer UID, to overwrite a profile.
Unimplemented statistics/currency remain zero. Optional actual local point
grants fill native txtScore@+34; hidden bonus stays-1 at+87.
"""
    if ((not observer and (recipient not in results or profile is None or len(profile)!=360 or
                           struct.unpack_from('<I',profile)[0]==0)) or
            (observer and (recipient in results or profile is not None))
            or not 1 <= len(results) <= 8 or any(r not in (0, 1, 2) for r in results.values())):
        raise ProtocolError('invalid no-award result')
    point_awards={} if point_awards is None else point_awards
    if (not isinstance(point_awards,dict) or not point_awards.keys()<=results.keys() or
            any(type(v) is not int or not 0<=v<=1000000 for v in point_awards.values())):
        raise ProtocolError('invalid result point awards')
    ranks={}
    if reborn_reports is not None:
        if (set(reborn_reports)!=set(results) or len(results)>6 or
                any(not isinstance(r,ReportSlot) or r.uid!=u for u,r in reborn_reports.items())):
            raise ProtocolError('invalid Mode16 result observations')
        #814860 sorts unsigned row+83. Equal native points use stable UID
        #display order; this ordinal is local presentation, not an old rank.
        ranks={u:i+1 for i,u in enumerate(sorted(results,key=lambda u:(-reborn_reports[u].reborn_points,u)))}
    rows = []
    for uid, outcome in results.items():
        row = bytearray(500)
        struct.pack_into('<Q', row, 0, uid)
        row[10] = outcome
        struct.pack_into('<I',row,34,point_awards.get(uid,0))
        row[33] = 1  # Do not claim a power-level protection award/warning.
        struct.pack_into('<i', row, 87, -1)
        if reborn_reports is not None:
            r=reborn_reports[uid]
            #Iterator value is key32 + row140;8153F0's offsets include that4.
            #Score0 is row67; Kill0 row71/73; integer HP0 totals row75/79.
            struct.pack_into('<iHHIII',row,67,r.reborn_points,*r.reborn_counts,
                             *struct.unpack_from('<HH',r.raw,4),ranks[uid])
        if not observer and uid == recipient:
            row[140:] = profile
        rows.append(bytes(row))
    return b''.join(rows)
