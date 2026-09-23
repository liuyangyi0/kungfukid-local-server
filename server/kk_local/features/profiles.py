"""Local profile rules, preserving opaque legacy bytes and Store return shapes."""
from contextlib import nullcontext
import struct
import unicodedata


class ProfileService:
    def __init__(self, store):
        self.store = store

    def provision(self, uid, account, nickname=None):
        """Offline defaults; registration may own the enclosing credentials transaction."""
        nickname = account if nickname is None else nickname
        if type(uid) is not int or not 0 < uid <= 0x7fffffffffffffff:
            raise ValueError('invalid account UID')
        if not isinstance(account, str) or not account.isascii() or not account.isalnum() or len(account) > 20:
            raise ValueError('account must be1..20 ASCII alphanumeric characters')
        encoded = nickname.encode('gbk')
        if not encoded or len(encoded) > 20 or b'\0' in encoded:
            raise ValueError('nickname must be1..20 GBK bytes')
        prior = self.store.profiles.account_name(uid)
        if prior:
            if prior[0] != account:
                raise ValueError('existing UID belongs to another account')
            return
        profile = bytearray(360)
        struct.pack_into('<I', profile, 0, 1)
        profile[4:25] = encoded.ljust(21, b'\0')
        profile[122] = profile[124] = 1
        items = [(121005, 12, 4), (131011, 13, 3), (141005, 14, 7),
                 (151005, 15, 2), (161005, 16, 6), (171005, 17, 5), (253030, 25, 8)]
        scope = nullcontext() if self.store.in_transaction else self.store.transaction(immediate=False)
        with scope:
            self.store.profiles.insert(uid, account, nickname, bytes(profile))
            for prop, kind, slot in items:
                instance = self.store.inventory.allocate_instance()
                row = bytearray(68)
                struct.pack_into('<IBI', row, 0, instance, kind, prop)
                struct.pack_into('<H', row, 17, slot)
                self.store.inventory.insert(uid, instance, bytes(row))

    def profile_word(self, uid, offset):
        if offset != 352:
            raise ValueError('unsupported profile word')
        row = self.store.profiles.status_word(uid)
        if row is None or len(row[0]) != 4:
            raise ValueError('profile word unavailable')
        return bytes(row[0])

    def rankings(self, category, uid):
        from ..rankings import local_rankings
        row = self.store.profiles.profile(uid)
        if row is None:
            raise ValueError('ranking actor missing')
        return local_rankings(self.store.profiles.ranking_rows(), category, uid, row[0])

    def nickname(self, uid):
        row = self.store.profiles.nickname(uid)
        if row is None:
            raise ValueError('unknown local account')
        return row[0]

    def snapshot(self, uid):
        account = self.store.profiles.account(uid)
        if not account:
            raise ValueError('unknown local account')
        records = [raw for _, raw in self.store.inventory.rows(uid, ordered=True)]
        return account[0], account[1], account[2], b''.join(records)

    def set_nickname(self, uid, nickname):
        encoded = nickname.encode('gbk')
        if not encoded or len(encoded) > 20 or b'\0' in encoded:
            raise ValueError('nickname must be 1..20 GBK bytes')
        with self.store.transaction('nested nickname transaction', immediate=False):
            _, _, old, _ = self.store.snapshot(uid)
            profile = bytearray(old)
            profile[4:25] = encoded.ljust(21, b'\0')
            self.store.profiles.rename(uid, nickname, bytes(profile))

    def rename(self, uid, nickname):
        """PROVISIONAL free local rename; exact-name uniqueness, lobby routing only."""
        encoded = nickname.encode('gbk')
        if (not encoded or len(encoded) > 20 or nickname != nickname.strip()
                or any(unicodedata.category(c).startswith('C') for c in nickname)):
            raise ValueError('invalid local nickname')
        with self.store.transaction('nested rename transaction'):
            if self.store.profiles.nickname_in_use(nickname, uid):
                raise ValueError('nickname already used')
            row = self.store.profiles.account(uid)
            if row is None:
                raise ValueError('unknown account')
            profile = bytearray(row[2])
            profile[4:25] = encoded.ljust(21, b'\0')
            self.store.profiles.rename(uid, nickname, bytes(profile))
            return row[1]
