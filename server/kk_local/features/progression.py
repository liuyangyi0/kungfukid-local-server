"""Local training/match policy, not recovered original-server economy formulas."""
import json
import struct


class ProgressionService:
    def __init__(self, store):
        self.store = store

    def next_battle(self):
        with self.store.transaction('nested battle identifier transaction'):
            value = self.store.progression.battle_counter() + 1
            if value > 0xffffffff:
                raise ValueError('battle identifier exhausted')
            self.store.progression.set_battle_counter(value)
        return value

    def training(self, uid, now, *, start=False):
        """Persist elapsed wall time without inferring XP or level progression."""
        if self.store.profiles.account_name(uid) is None:
            raise ValueError('unknown local account')
        with self.store.transaction('nested training transaction', immediate=False):
            self.store.progression.ensure_training(uid)
            if start:
                self.store.progression.start_training(uid, int(now))
        started = self.store.progression.training(uid)[0]
        minutes = 0 if started is None else min(0x7fffffff // 60, max(0, int(now) - started) // 60)
        return minutes, started is not None

    def claim_training(self, uid, now, *, points_per_hour=100, points_cap=2400):
        """Credit whole hours once; only profile+245 changes, +249 is preserved."""
        if (type(points_per_hour) is not int or type(points_cap) is not int or
                not 1 <= points_per_hour <= points_cap <= 1000000):
            raise ValueError('invalid local training reward policy')
        with self.store.transaction('nested reward transaction'):
            _, _, raw, _ = self.store.snapshot(uid)
            training = self.store.progression.training(uid)
            started = training[0] if training else None
            hours = 0 if started is None else max(0, int(now) - started) // 3600
            score, second = struct.unpack_from('<II', raw, 245)
            if not hours or self.store.progression.training_claimed(uid, started):
                return None
            awarded = min(hours * points_per_hour, points_cap, 0x7fffffff - score)
            if awarded <= 0:
                return None
            profile = bytearray(raw)
            struct.pack_into('<I', profile, 245, score + awarded)
            self.store.profiles.update_profile(uid, bytes(profile))
            self.store.progression.record_training_claim(uid, started, int(now), awarded)
            self.store.progression.clear_training(uid)
            return dict(points=score + awarded, second=second, awarded=awarded)

    def award_match_points(self, battle, outcomes, rates, *, mode=None, quest_templates=None):
        """One atomic outcome signature; retries return current profiles, not old copies."""
        if type(battle) is not int or not 0 < battle <= 0x7fffffffffffffff:
            raise ValueError('invalid reward battle')
        if (not isinstance(outcomes, dict) or not 1 <= len(outcomes) <= 8 or
                any(type(u) is not int or not 0 < u <= 0x7fffffffffffffff or
                    type(v) is not int or v not in (0, 1, 2) for u, v in outcomes.items())):
            raise ValueError('invalid reward outcomes')
        if (not isinstance(rates, dict) or set(rates) != {0, 1, 2} or
                any(type(k) is not int or type(v) is not int or not 0 <= v <= 1000000
                    for k, v in rates.items())):
            raise ValueError('invalid match point rates')
        if mode is not None and (type(mode) is not int or mode not in (0, 1, 2, 3, 16)):
            raise ValueError('unsupported settlement mode')
        facts = [sorted(outcomes.items()), sorted(rates.items())]
        if mode is not None:
            facts.append(mode)  # Preserve legacy no-mode receipt signatures.
        signature = json.dumps(facts, separators=(',', ':'))
        with self.store.transaction('nested match reward transaction'):
            prior = self.store.progression.match_receipt(battle)
            if prior is not None:
                if prior[0] != signature:
                    raise ValueError('changed match reward retry')
                if set(self.store.progression.match_awards(battle)) != set(outcomes):
                    raise ValueError('incomplete reward receipt')
                return {u: self.store.snapshot(u)[2] for u in outcomes}
            profiles = {}
            from ..ordinary_quests import tracking_ready, add_counters
            count_ordinary = mode in (0, 1, 2, 3) and len(outcomes) >= 2 and tracking_ready(self.store, quest_templates or {})
            self.store.progression.record_match(battle, signature)
            for uid, outcome in sorted(outcomes.items()):
                profile = bytearray(self.store.snapshot(uid)[2])
                score = struct.unpack_from('<I', profile, 245)[0]
                if score > 0x7fffffff:
                    raise ValueError('unsupported profile score range')
                awarded = min(rates[outcome], 0x7fffffff - score)
                struct.pack_into('<I', profile, 245, score + awarded)
                if count_ordinary:
                    add_counters(profile, mode, outcome)
                profiles[uid] = bytes(profile)
                self.store.profiles.update_profile(uid, profiles[uid])
                self.store.progression.record_match_award(battle, uid, outcome, awarded)
            if mode in (0, 1, 2, 3):
                from ..quest_rewards import settled_locked
                settled_locked(self.store, battle, mode, outcomes, quest_templates or {})
            return profiles
