"""Explicit OFFLINE repair on a backed-up database; never an automatic migration."""
import struct


def repair_duplicate_weapon_instances(store):
    """Keep the first UID's weapon ID; refuse history that cannot safely be rekeyed."""
    changes = []
    with store.transaction('nested inventory repair'):
        seen = set()
        for uid, instance, raw in store.inventory.all_items():
            if raw[4] != 25:
                continue
            if instance not in seen:
                seen.add(instance)
                continue
            if store.maintenance.consumption_exists(uid, instance):
                raise ValueError('duplicate weapon has consumption history; explicit migration required')
            if store.maintenance.linked_history_exists(uid, instance):
                raise ValueError('duplicate weapon has lease/mail history; explicit migration required')
            if any(struct.unpack_from('<I',record)[0] == instance for record in store.commerce.purchase_items(uid)):
                raise ValueError('duplicate weapon has purchase history; explicit migration required')
            new = store.inventory.allocate_instance()
            record = bytearray(raw)
            struct.pack_into('<I', record, 0, new)
            entitled = store.inventory.permanent(uid, instance)
            store.inventory.insert(uid, new, bytes(record))
            if entitled:
                store.inventory.add_permanent(uid, new, strict=True)
            store.inventory.delete(uid, instance)
            changes.append(dict(uid=uid, old_instance=instance, new_instance=new))
    return changes
