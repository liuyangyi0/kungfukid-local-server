"""Transactional local account snapshots; opaque record bytes are preserved."""
from pathlib import Path
import sqlite3
import struct
import time
from contextlib import contextmanager

from .features.inventory import (InventoryService,EQUIPMENT_SLOTS,PERMANENT_WEAPON_DISPLAY_MINUTES,
                                 permanent_weapon_record,permanent_equipment_record)
from .features.catalog import CatalogService
from .features.profiles import ProfileService
from .features.progression import ProgressionService
from .storage.inventory import InventoryRepository
from .storage.commerce import CommerceRepository
from .storage.mail import MailRepository
from .storage.profiles import ProfileRepository
from .storage.progression import ProgressionRepository
from .storage.quests import QuestRepository
from .storage.titles import TitleRepository
from .storage.renewal import RenewalRepository
from .storage.talisman import TalismanRepository
from .storage.upgrades import UpgradeRepository
from .storage.maintenance import MaintenanceRepository
from .storage.transactions import transaction


class Store:
    def __init__(self, path: str, *, public=False):
        if public and (path==':memory:' or not Path(path).is_file()):raise ValueError('existing migrated public database required')
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.public_mode=public;self._commit_callbacks=[];self.transaction_observer=None
        try:
            if public:
                from .storage.public_access import version,VERSION
                if version(self.db)!=VERSION:raise ValueError('public_schema_migration_required')
                self.db.execute('PRAGMA busy_timeout=0');self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            from .storage.schema import initialize
            initialize(self.db)
            self.inventory=InventoryRepository(self.db)
            self.commerce=CommerceRepository(self.db)
            self.mail=MailRepository(self.db)
            self.profiles=ProfileRepository(self.db)
            self.progression=ProgressionRepository(self.db)
            self.quests=QuestRepository(self.db)
            self.titles=TitleRepository(self.db)
            self.renewal=RenewalRepository(self.db)
            self.talisman=TalismanRepository(self.db)
            self.upgrades=UpgradeRepository(self.db)
            self.maintenance=MaintenanceRepository(self.db)
            self._inventory_service=InventoryService(self)
            self._catalog_service=CatalogService(self)
            self._profile_service=ProfileService(self)
            self._progression_service=ProgressionService(self)
            self._auth_repository=None
        except BaseException:
            self.db.close()
            raise

    @property
    def in_transaction(self):
        return self.db.in_transaction

    @property
    def public_access(self):
        from .storage.public_access import PublicAccessRepository
        return PublicAccessRepository(self.db)

    def migrate_public(self):
        from .storage.public_schema import migrate
        migrate(self.db)

    @property
    def authentication(self):
        # Optional schema stays lazy: offline modes do not create auth tables.
        if self._auth_repository is None:
            from .storage.schema import initialize_auth
            from .storage.auth import AuthRepository
            initialize_auth(self.db)
            self._auth_repository=AuthRepository(self.db)
        return self._auth_repository

    @contextmanager
    def transaction(self,message='nested transaction',*,immediate=True):
        if self.db.in_transaction:raise ValueError(message)
        self._commit_callbacks=[];began=time.monotonic()
        try:
            with transaction(self.db,message,immediate=immediate):yield
        except BaseException:self._commit_callbacks=[];raise
        finally:
            if self.transaction_observer:self.transaction_observer(time.monotonic()-began)
        callbacks=self._commit_callbacks;self._commit_callbacks=[]
        for callback in callbacks:callback()

    def after_commit(self,callback):
        if not self.db.in_transaction:raise ValueError('commit callback requires transaction')
        self._commit_callbacks.append(callback)

    def _upgrade_quest_progress(self):
        #Private compatibility entry; implementation has one authority.
        from .storage.schema import upgrade_quest_progress
        upgrade_quest_progress(self.db)

    def replace_shop_catalog(self, category, variant, records):
        return self._catalog_service.replace(category,variant,records)

    def shop_records(self, category, variant):
        return self._catalog_service.records(category,variant)

    def shop_cache_records(self):
        return self._catalog_service.cache()

    def shop_item_records(self, kind, item_id):
        return self._catalog_service.by_item(kind,item_id)

    def gold_balance(self, uid):
        return self.commerce.balance(uid,'gold')

    def set_gold_balance(self, uid, balance):
        if type(balance) is not int or not 0<=balance<=2147483647:
            raise ValueError('invalid gold balance')
        with self.transaction('nested wallet transaction',immediate=False):
            self.commerce.set_balance(uid,'gold',balance,replace=True)

    def _gold_offer_record(self, key):
        return self._catalog_service.strict_gold_offer(key)

    def enable_gold_offer(self, key, grant_template):
        """Compatibility entry: retain the original strict gold admission."""
        self._gold_offer_record(key)
        from .shop import enable_offer
        enable_offer(self,key,grant_template)

    def purchase_gold_once(self, uid, operation_id, request):
        """Compatibility return shape; all actual purchases share one transaction."""
        if not isinstance(request,bytes) or len(request)!=169 or struct.unpack_from('<I',request)[0]!=111:
            raise ValueError('gold purchase request required')
        from .shop import purchase
        currency,value,item,record=purchase(self,uid,operation_id,request)
        if currency!='gold':raise ValueError('gold receipt expected')
        return value,item,record

    def seed_local(self):
        return self.provision_local(1001, 'KKLocal', 'KKLocal')

    def provision_local(self, uid, account, nickname=None):
        """Explicit offline account provisioning, not old SDK/password authentication."""
        return self._profile_service.provision(uid,account,nickname)

    def _allocate_inventory_instance(self):
        return self.inventory.allocate_instance()

    def repair_duplicate_weapon_instances(self):
        """Explicit offline repair; preserve the compatibility entry."""
        from .features.inventory_repair import repair_duplicate_weapon_instances
        return repair_duplicate_weapon_instances(self)

    def profile_word(self, uid, offset):
        return self._profile_service.profile_word(uid,offset)

    def local_rankings(self, category, uid):
        return self._profile_service.rankings(category,uid)

    def nickname(self, uid):
        return self._profile_service.nickname(uid)

    def snapshot(self, uid: int):
        return self._profile_service.snapshot(uid)

    def set_nickname(self, uid: int, nickname: str):
        return self._profile_service.set_nickname(uid,nickname)

    def rename_local(self, uid, nickname):
        """PROVISIONAL free local rename; exact-name uniqueness, lobby routing only."""
        return self._profile_service.rename(uid,nickname)

    def next_battle(self):
        return self._progression_service.next_battle()

    def training(self, uid: int, now: float, *, start=False):
        """Persist real elapsed wall time; no invented reward/level progression."""
        return self._progression_service.training(uid,now,start=start)

    def close(self):
        self.db.close()

    def claim_training(self, uid, now, *, points_per_hour=100, points_cap=2400):
        return self._progression_service.claim_training(uid,now,points_per_hour=points_per_hour,points_cap=points_cap)

    def award_match_points(self,battle,outcomes,rates,*,mode=None,quest_templates=None):
        return self._progression_service.award_match_points(battle,outcomes,rates,mode=mode,quest_templates=quest_templates)

    def match_point_awards(self,battle):
        """Actual saturated grant values for native4120 score display."""
        return self.progression.match_awards(battle)

    def apply_grant(self, plan):
        return self._inventory_service.apply_grant(plan)

    def set_weapons_permanent(self, uid):
        return self._inventory_service.set_weapons_permanent(uid)

    def equip(self, uid, instance, slot):
        return self._inventory_service.equip(uid,instance,slot)

    def unequip(self, uid, instance):
        return self._inventory_service.unequip(uid,instance)

    def weapon_switch_snapshot(self, uid):
        return self._inventory_service.weapon_switch_snapshot(uid)

    def consumable(self, uid, *, instance=None, slot=None):
        return self._inventory_service.consumable(uid,instance=instance,slot=slot)

    def consume_once(self, uid, battle, sequence, instance, signature, intent):
        return self._inventory_service.consume_once(uid,battle,sequence,instance,signature,intent)

    def consumable_slots(self, uid):
        return self._inventory_service.consumable_slots(uid)
