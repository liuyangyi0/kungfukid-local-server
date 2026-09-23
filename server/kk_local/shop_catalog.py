"""9080 framing and a lossless view of native 108-byte shop records.

Refs: A2D910 (envelope), 846BD0 (filter), 84A5A0 (display).
Unknown bytes are retained, never inferred as padding or generated as zeros.
This is not a purchase contract or an authorization to offer captured goods.
"""
from dataclasses import dataclass
import struct
from .wire import Message, ProtocolError, MAX_FRAME

RECORD_SIZE = 108
MAX_RECORDS = (MAX_FRAME - 32 - 6) // RECORD_SIZE  # local bounded framing policy


@dataclass(frozen=True)
class ShopRecord:
    raw: bytes

    def __post_init__(self):
        if not isinstance(self.raw, bytes) or len(self.raw) != RECORD_SIZE:
            raise ProtocolError('shop record must be 108 immutable bytes')

    def u32(self, offset):
        return struct.unpack_from('<I', self.raw, offset)[0]

    @property
    def item_id(self):
        return self.u32(5)

    @property
    def display_price(self):
        # 84A5A0 selects Gold.png by old gold price != 0, NOT current price.
        gold = self.u32(30) != 0
        return ('gold' if gold else 'ticket',
                self.u32(30 if gold else 38), self.u32(34 if gold else 42))

    @property
    def decoded_fields(self):
        return dict(item_kind=self.raw[4],item_id=self.item_id, secondary_key=self.u32(9),
                    filter_mask=self.u32(14), use_value_22=self.u32(22),
                    use_value_26=self.u32(26), old_gold=self.u32(30),
                    current_gold=self.u32(34), old_ticket=self.u32(38),
                    current_ticket=self.u32(42), sale_marker=self.raw[46],
                    sign_code=self.raw[47], admission_flag=self.raw[48],
                    credit_offset_enabled=self.raw[49],associated_catalog_key=self.u32(77),
                    requirement_kind=self.raw[83],requirement_value=self.requirement[1],
                    score_kind=self.u32(88),score_value_raw=self.u32(92))

    @property
    def requirement(self):
        # 84A5A0 ->847F40: kind1 uses byte13; other kinds use dword84.
        kind=self.raw[83]
        return kind,self.raw[13] if kind==1 else self.u32(84)

    def admitted_by_client(self, category):
        return self.raw[48] != 0 or category == 19


def decode_catalog(payload: bytes):
    if len(payload) < 6:
        raise ProtocolError('shop header truncated')
    category, variant, count = struct.unpack_from('<BBI', payload)
    if count > MAX_RECORDS or len(payload) != 6 + count * RECORD_SIZE:
        raise ProtocolError('shop count/length mismatch')
    return category, variant, tuple(ShopRecord(bytes(payload[i:i + RECORD_SIZE]))
                                   for i in range(6, len(payload), RECORD_SIZE))


def encode_catalog(category: int, variant: int, records=()):
    if type(category) is not int or type(variant) is not int or not (0 <= category <= 255 and 0 <= variant <= 255):
        raise ProtocolError('catalog selector range')
    # Only complete records are accepted; no partial dict -> zero fill shortcut.
    body = bytearray()
    count = 0
    for record in records:
        if not isinstance(record, ShopRecord):
            raise ProtocolError('complete ShopRecord required')
        count += 1
        if count > MAX_RECORDS:
            raise ProtocolError('shop catalog too large')
        body.extend(record.raw)
    return Message(9080, struct.pack('<BBI', category, variant, count) + body)


def main():
    """Explicit admin replacement from an already decoded native9080 payload."""
    import argparse
    from pathlib import Path
    from .store import Store
    p=argparse.ArgumentParser(description='Explicit local shop administration; never a network client operation')
    p.add_argument('--database',required=True)
    operations=p.add_mutually_exclusive_group(required=True)
    operations.add_argument('--import-9080-payload')
    operations.add_argument('--enable-gold-key',type=int)
    operations.add_argument('--set-gold',nargs=2,type=int,metavar=('UID','BALANCE'))
    p.add_argument('--grant-template',help='Complete68-byte unequipped item record for enable-gold-key')
    p.add_argument('--replace',action='store_true',required=True,
                   help='Explicitly replace only the selectors contained in this payload')
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('Existing database required')
    if args.grant_template and args.enable_gold_key is None:p.error('grant-template only applies to enable-gold-key')
    if args.enable_gold_key is not None and not args.grant_template:p.error('Complete grant-template required')
    if args.import_9080_payload:
        with Path(args.import_9080_payload).open('rb') as f:data=f.read(MAX_FRAME+1)
        if len(data)>MAX_FRAME:p.error('Payload too large')
        category,variant,records=decode_catalog(data)
    store=Store(args.database)
    try:
        if args.import_9080_payload:
            n=store.replace_shop_catalog(category,variant,(r.raw for r in records))
            print(f'Imported {n} preview records for {category}/{variant}; changed offers require explicit qualification.')
        elif args.enable_gold_key is not None:
            with Path(args.grant_template).open('rb') as f:grant=f.read(69)
            store.enable_gold_offer(args.enable_gold_key,grant)
            print('Basic gold offer explicitly enabled; native end-to-end verification still required.')
        else:
            store.set_gold_balance(*args.set_gold)
            print('Local administrator gold balance updated; reconnect client to refresh bootstrap balance.')
    finally:store.close()


if __name__=='__main__':main()
