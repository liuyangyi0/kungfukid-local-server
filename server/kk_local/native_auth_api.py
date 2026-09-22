"""Own-launcher account API over TLS, no game-data tunnel or foreign protocol."""
import asyncio
import ssl
from .auth import AuthError
from .auth_service import AuthServer


class NativeAuthAPI(AuthServer):
    event_operations=AuthServer.event_operations+('authorize_game','client_ready','entry_status','cancel_game')
    def __init__(self,admission,*,port=7999,context=None,event_sink=None):
        super().__init__(admission.auth,port=port,event_sink=event_sink)
        self.admission=admission;self.context=context

    async def start(self):
        if self.context is None or self.context.minimum_version<ssl.TLSVersion.TLSv1_2:
            raise ValueError('native auth requires TLS1.2+ and a configured certificate')
        self.server=await asyncio.start_server(self._accept,'127.0.0.1',self.port,
                                              ssl=self.context,ssl_handshake_timeout=10,limit=8192)
        self.port=self.server.sockets[0].getsockname()[1];return self

    async def dispatch(self,request):
        if not isinstance(request,dict) or set(request)!={'schema','operation','arguments'} or request['schema']!='kk-local-auth-v1':raise AuthError('invalid_request')
        op=request['operation'];args=request['arguments']
        if op in ('bind_client','status'):raise AuthError('native_pid_api_not_available')
        if op=='authorize_game':
            if not isinstance(args,dict) or set(args)!={'ticket','region_id'}:raise AuthError('invalid_request')
            return self.admission.issue(args['ticket'],args['region_id'])
        if op=='client_ready':
            if not isinstance(args,dict) or set(args)!={'session','game_credential'}:raise AuthError('invalid_request')
            return self.admission.set_ready(args['session'],args['game_credential'])
        if op=='entry_status':
            if not isinstance(args,dict) or set(args)!={'session','uid'}:raise AuthError('invalid_request')
            digest,uid=self.manager._session(args['session'])
            if type(args['uid']) is not int or args['uid']!=uid:raise AuthError('native_identity_mismatch')
            grant=self.admission.by_uid.get(uid)
            if grant is None or grant.session_digest!=digest:raise AuthError('native_grant_revoked')
            self.admission.validate(grant)
            c=grant.engine.game if grant.engine else None
            return dict(uid=uid,sdk_admitted=grant.sdk_admitted,client_ready=grant.ready,
                        stage=c.phase.value if c else 'preparing',lobby_ready=grant.lobby_ready and c is not None and c.phase.value=='lobby')
        if op=='cancel_game':
            if not isinstance(args,dict) or set(args)!={'session','game_credential'}:raise AuthError('invalid_request')
            grant=self.admission.for_session(args['session'],args['game_credential'])
            self.admission.release(grant)
            return dict(cancelled=True)
        result=await super().dispatch(request)
        if op=='regions':
            result=[dict(id=r['id'],name=r['name'],host=self.admission.host,game_port=self.admission.game_port) for r in result]
        elif op=='select_region':
            result['region']['host']=self.admission.host;result['region']['game_port']=self.admission.game_port
        elif op=='logout':self.admission.prune()
        return result
