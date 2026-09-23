"""Social handlers; preserve existing phase gates and reply ordering."""
from ..chat import public_text, private_text, chat_reply
from ..wire import ProtocolError


def handle(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident==5002 and c.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE):
        try:raw=public_text(p)
        except ProtocolError:return engine.chat_rejected('public','invalid_request')
        now=engine.clock()
        if now-engine.last_public_chat<1:return engine.chat_rejected('public','rate_limited')
        engine.last_public_chat=now
        reply=chat_reply(c.uid,engine.store.nickname(c.uid),raw)
        if engine.hub is not None:
            if engine.room is not None:
                engine.hub.broadcast(engine.room,reply,exclude=c.uid)
            else:
                for peer in engine.hub.engines.values():
                    if peer is not engine and peer.game is not None and peer.game.phase==Phase.LOBBY:
                        peer.enqueue(reply)
        engine.last_chat_event=dict(event='chat_result',channel='public',outcome='accepted',reason='local_echo')
        return [reply]
    if ident==5000 and c.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE):
        try:recipient,raw=private_text(p)
        except ProtocolError:return engine.chat_rejected('private','invalid_request')
        if engine.hub is None:return engine.chat_rejected('private','routing_unavailable')
        peers=[peer for peer in engine.hub.engines.values()
               if peer is not engine and peer.game is not None
               and peer.game.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE)
               and peer.store.nickname(peer.account_uid)==recipient]
        if not peers:return engine.chat_rejected('private','recipient_unavailable')
        if len(peers)!=1:return engine.chat_rejected('private','recipient_ambiguous')
        if peers[0].delivery_failed:return engine.chat_rejected('private','recipient_queue_failed')
        now=engine.clock()
        if now-engine.last_public_chat<1:return engine.chat_rejected('private','rate_limited')
        engine.last_public_chat=now
        reply=chat_reply(c.uid,engine.store.nickname(c.uid),raw,recipient=recipient)
        peers[0].enqueue(reply)
        if peers[0].delivery_failed:return engine.chat_rejected('private','recipient_queue_failed')
        engine.last_chat_event=dict(event='chat_result',channel='private',outcome='accepted',reason='queued')
        return [reply]
    return None
