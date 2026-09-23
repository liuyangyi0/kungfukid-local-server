import struct
import tempfile
from pathlib import Path
import unittest

from server.kk_local.store import Store
from server.kk_local.engine import Engine,Connection,Phase,Room
from server.kk_local.wire import Message


def event(sequence, slot=27, serial=7, uid=1001, elapsed=400):
    data=bytearray(75)
    struct.pack_into('<IQ',data,0,8289,uid)
    data[12:14]=b'\x01\x01'
    struct.pack_into('<II',data,19,sequence,elapsed)
    struct.pack_into('<I',data,39,slot)
    struct.pack_into('<QII',data,59,uid,1,serial)
    return Message(8071,bytes(data))


class ConsumptionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'account.sqlite3')
        self.store=Store(self.path);self.store.seed_local()
        self.store.apply_grant(dict(schema='kk-local-inventory-grant-v1',uid=1001,
            grant_id='consume-test',rows=[[643003,64,2],[643006,64,100]]))
        rows=self.store.db.execute('SELECT instance,record FROM inventory WHERE uid=1001').fetchall()
        self.ids={struct.unpack_from('<I',r,5)[0]:i for i,r in rows}
        self.first=self.ids[643003];self.second=self.ids[643006]
        self.store.equip(1001,self.first,27)
        self.engine=Engine(self.store)
        self.c=Connection(1,Phase.BATTLE,1001)
        self.engine.game=self.c
        self.engine.room=Room(1001,b'',b'',1,7)

    def tearDown(self):
        self.store.close();self.temp.cleanup()

    def count(self,instance=None):
        return struct.unpack_from('<H',self.store.consumable(1001,instance=instance or self.first)[1],23)[0]

    def intent(self,instance=None):
        return self.engine.handle(self.c,Message(4200,struct.pack('<I',instance or self.first)))

    def test_pair_atomic_snapshot_and_duplicate(self):
        self.assertEqual(self.intent(),[])
        self.assertEqual(self.count(),2)
        response=self.engine.handle(self.c,event(20))
        self.assertEqual([m.id for m in response],[4210])
        self.assertEqual(len(response[0].payload),34)
        self.assertEqual(struct.unpack_from('<Q',response[0].payload)[0],1001)
        self.assertEqual(struct.unpack_from('<I',response[0].payload,8)[0],self.first)
        self.assertEqual(struct.unpack_from('<I',response[0].payload,12)[0],0)
        self.assertEqual(struct.unpack_from('<H',response[0].payload,26)[0],1)
        self.assertEqual(self.count(),1)
        self.intent();self.engine.handle(self.c,event(20))
        self.assertEqual(self.count(),1)
        self.assertEqual(self.engine.last_consume_event['event'],'consume_duplicate')

    def test_different_sequence_is_another_use_and_zero_never_wraps(self):
        for seq in (1,2):
            self.intent();self.engine.handle(self.c,event(seq))
        self.assertEqual(self.count(),0)
        self.intent();self.engine.handle(self.c,event(3))
        self.assertEqual(self.count(),0)
        self.assertEqual(self.engine.last_consume_event['event'],'consume_rejected')
        self.engine.handle(self.c,event(2))
        self.assertEqual(self.engine.last_consume_event['event'],'consume_duplicate')

    def test_wrong_context_and_missing_intent_do_not_bill(self):
        self.engine.handle(self.c,event(1))
        self.assertEqual(self.count(),2)
        self.intent()
        for packet in (event(1,uid=2),event(1,serial=8),event(1,slot=9),Message(8071,event(1).payload[:-1])):
            self.engine.handle(self.c,packet)
            self.assertEqual(self.count(),2)
        self.engine.handle(self.c,Message(4200,struct.pack('<I',99999)))
        self.assertEqual(self.count(),2)
        self.c.phase=Phase.LOBBY
        self.intent();self.engine.handle(self.c,event(1))
        self.assertEqual(self.count(),2)

    def test_conflicting_replay_and_restart(self):
        self.intent();self.engine.handle(self.c,event(3))
        self.intent();self.engine.handle(self.c,event(3,elapsed=401))
        self.assertEqual(self.engine.last_consume_event['event'],'consume_rejected')
        self.assertEqual(self.count(),1)
        self.store.close();self.store=Store(self.path)
        self.assertEqual(self.count(),1)
        self.engine.store=self.store
        self.engine.handle(self.c,event(3))
        self.assertEqual(self.count(),1)

    def test_second_slot_and_distinct_secondary_fields(self):
        self.store.equip(1001,self.second,28)
        r=bytearray(self.store.consumable(1001,instance=self.second)[1])
        struct.pack_into('<I',r,9,12345)
        self.store.db.execute('UPDATE inventory SET record=? WHERE instance=?',(bytes(r),self.second));self.store.db.commit()
        self.intent(self.second)
        response=self.engine.handle(self.c,event(6,slot=28))[0].payload
        self.assertEqual(struct.unpack_from('<I',response,22)[0],12345)
        self.assertEqual(struct.unpack_from('<H',response,30)[0],99)
        self.assertEqual(struct.unpack_from('<H',response,26)[0],2)

    def test_exit_clears_intent_and_8290_is_not_ack(self):
        self.intent()
        wrong=bytearray(event(1).payload);struct.pack_into('<I',wrong,0,8290)
        self.engine.handle(self.c,Message(8071,bytes(wrong)))
        self.assertEqual(self.count(),2)
        self.engine.handle(self.c,Message(3110))
        self.assertFalse(self.engine.consume_intents)


if __name__=='__main__':unittest.main()
