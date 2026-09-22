"""Lobby handlers; preserve existing phase gates and reply ordering."""
import struct
from .. import packets
from ..wire import Message
from ..layouts import decode_known
from ..chat import system_notice


def handle(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident==9070 and c.phase in (Phase.LOBBY,Phase.ROOM):
        engine.require(len(p)==2,'catalog selector length')
        from ..shop import wallet_packets
        #Native shop checks its local wallet before producing a buy request.
        #Refresh only a configured shop; old read-only preview remains unchanged.
        wallets=wallet_packets(engine.store,c.uid) if engine.store.commerce.has_offers() else []
        return wallets+[packets.encode_catalog(p[0],p[1],engine.store.shop_records(p[0],p[1]))]
    if ident in (21410,21412):
        from ..weapon_upgrade import handle as upgrade_weapon
        return upgrade_weapon(engine,c,message)
    if ident==4126:
        from ..title_rewards import handle_claim
        return handle_claim(engine,c,p)
    if ident in (6000,6001,6002,6050,6080,6051,6081,6311,6052,6082,6312):
        from ..quests import handle as quest_request
        return quest_request(engine,c,message)
    if ident in (4202,4204):
        from ..talisman_repair import handle as repair_talisman
        return repair_talisman(engine,c,message)
    if ident in (2540,2560) and c.phase==Phase.LOBBY:
        from ..menu_layouts import decode_menu_request
        fields=decode_menu_request(ident,p)
        if fields['rank_category'] not in range(13):
            engine.record_unknown(c,ident,p);return []
        # Never use the unresolved request identity to read another profile.
        directory,own=engine.store.local_rankings(fields['rank_category'],c.uid)
        return [directory if ident==2540 else own]
    if ident==20546 and c.phase in (Phase.LOBBY,Phase.ROOM):
        engine.require(not p,'profile field query length')
        # A2C170 writes the four bytes back to profile+352. Preserve bits;
        # the business meaning and downstream UI flags are not re-invented.
        return [Message(20547,engine.store.profile_word(c.uid,352))]
    if ident in (20561,20563,20565) and c.phase==Phase.LOBBY:
        fields=decode_known(ident,p)
        # Only the unconfigured-event branch is supported. This is not an
        # implemented spending leaderboard, season calculation or prize grant.
        if ident==20561:return [packets.inactive_wealth_page(fields['page'])]
        if ident==20563:return [packets.inactive_wealth_self()]
        return [Message(20566,bytes(48))]
    if ident==1540 and c.phase in (Phase.LOBBY,Phase.ROOM):
        engine.require(not p,'shop cache query length')
        try:records=engine.store.shop_cache_records()
        except ValueError:
            return [system_notice('[本地服务] 商品目录存在冲突或超出容量，暂不可用。')]
        return [Message(1550,b''.join(record.raw for record in records))]
    if ident==1500 and c.phase in (Phase.LOBBY,Phase.ROOM):
        fields=decode_known(ident,p)
        if fields['query_mode']==1:
            from ..renewal import handle as handle_renewal
            return handle_renewal(engine,c,message)
        if fields['query_mode']!=0:
            engine.record_unknown(c,ident,p);return []  # renewal not qualified
        rows=engine.store.shop_item_records(fields['item_kind'],fields['item_id'])
        return [Message(1510,b''.join(r.raw for r in rows))]
    if ident==9006 and c.phase in (Phase.LOBBY,Phase.ROOM):
        try:
            fields=decode_known(ident,p)
            if c.phase!=Phase.LOBBY or fields['actor']!=c.uid:raise ValueError('rename identity/phase')
            old=engine.store.rename_local(c.uid,fields['nickname'])
        except ValueError:return [packets.nickname_rejected()]
        return [packets.nickname_changed(c.uid,old,fields['nickname'])]
    if ident in (9040,9041) and c.phase in (Phase.LOBBY,Phase.ROOM):
        engine.require(len(p)==169,'purchase request length')
        # Never trust quoted prices, names or recipient fields as authority.
        engine.layout_observations[ident]+=1
        if ident==9040:
            operation=f'{engine.transaction_namespace}:{c.number}:{c.command_sequence}'
            from ..shop import purchase,purchase_packets
            try:result=purchase(engine.store,c.uid,operation,p)
            except ValueError:return [packets.purchase_unavailable()]
            return purchase_packets(result)
        return [packets.purchase_unavailable()]
    if ident in (9090,9091) and c.phase in (Phase.LOBBY,Phase.ROOM):
        from ..shop import gift
        #A2A120 handles56 explicitly; never index an unknown error code.
        failed=[Message(9110,struct.pack('<H',56))]
        if ident!=9090:return failed
        try:
            value,target,mail,created=gift(engine.store,c.uid,f'{engine.transaction_namespace}:{c.number}:{c.command_sequence}',p)
        except (ValueError,UnicodeError):return failed
        #Offline recipients read1310 after login; no unsolicited full list.
        if created and engine.hub:
            for peer in engine.hub.engines.values():
                if peer.account_uid==target and peer.game and peer.game.phase in (Phase.LOBBY,Phase.ROOM):
                    peer.enqueue(system_notice('[本地服务] 收到商城赠送，请到消息中心查看。'))
        return [Message(1230,struct.pack('<I',value)),Message(9100,b'\1')]
    if ident in (1300,1320,1340,2171):
        from ..mailbox import handle as handle_mail
        return handle_mail(engine,c,message)
    if ident in (1400,1420,1440):
        from ..renewal import handle as handle_renewal
        return handle_renewal(engine,c,message)
    if ident==20360 and c.phase in (Phase.LOBBY,Phase.ROOM):
        engine.require(len(p)==12,'ranked record request length')
        if struct.unpack_from('<Q',p)[0]!=c.uid:
            engine.record_unknown(c,ident,p)
            return []  # Other-player lookup and its denial are not qualified.
        return [packets.no_local_ranked_season()]
    return None
