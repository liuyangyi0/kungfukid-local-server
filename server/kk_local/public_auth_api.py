"""Platform-independent TLS account endpoint. No PID, SDK binary or VM check."""
import asyncio
import contextlib
import json
import struct
import time
from .native_auth_api import NativeAuthAPI
from .public_policy import PublicPolicy,ConnectionBudget,FairRate
from .public_listener import BoundedListener
from .auth import AuthError,normalize_account

def strict_json(data):
    from .app.configuration import _unique_object
    # Bound structural nesting before invoking recursive JSON decoding.
    depth=0;quoted=False;escape=False
    for ch in data:
        if quoted:
            if escape:escape=False
            elif ch==92:escape=True
            elif ch==34:quoted=False
        elif ch==34:quoted=True
        elif ch in (91,123):
            depth+=1
            if depth>16:raise AuthError('invalid_request')
        elif ch in (93,125):depth-=1
    try:return json.loads(data.decode('utf-8'),object_pairs_hook=_unique_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
    except (UnicodeError,ValueError,RecursionError):raise AuthError('invalid_request') from None

class PublicAuthAPI(NativeAuthAPI):
    def __init__(self,admission,*,host='127.0.0.1',port=0,context,policy,event_sink=None):
        super().__init__(admission,port=port,context=context,event_sink=event_sink)
        self.host=host;self.policy=policy;self.connections=ConnectionBudget(policy)
        self.login_ip=FairRate(policy.login_ip_rate,policy.login_ip_burst);self.login_account=FairRate(policy.login_account_per_minute/60,policy.login_account_per_minute)
        self.register_ip=FairRate(policy.registration_ip_per_hour/3600,policy.registration_ip_per_hour);self.register_global=FairRate(policy.registration_global_per_hour/3600,policy.registration_global_burst,limit=1)
        self.general=FairRate(policy.auth_request_rate,policy.auth_request_burst);self.session_rate=FairRate(policy.session_request_rate,policy.session_request_burst)
    async def start(self):
        import ssl
        if self.context is None or self.context.minimum_version<ssl.TLSVersion.TLSv1_2:raise ValueError('TLS required')
        self.server=await BoundedListener(self.host,self.port,self.accept_public,self.connections,ssl_context=self.context,tls_seconds=self.policy.tls_seconds,stream_limit=8192).start()
        self.port=self.server.port;return self
    async def dispatch_peer(self,request,peer):
        if not isinstance(request,dict) or set(request)!={'schema','operation','arguments'} or request['schema']!='kk-local-auth-v1':raise AuthError('invalid_request')
        op=request['operation'];args=request['arguments']
        if not isinstance(op,str) or not isinstance(args,dict):raise AuthError('invalid_request')
        if not self.general.take(peer):raise AuthError('rate_limited')
        if op=='capabilities':
            if args:raise AuthError('invalid_request')
            result=self.policy.capabilities()
            result['features']['correlated_hit_receipts']=self.admission.hub.combat_catalog is not None
            result['features']['attacker_effect_receipts']=False
            return result
        if op=='register':
            if not {'account','password'}<=set(args) or set(args)-{'account','password','nickname','invite_code'}:raise AuthError('invalid_request')
            from .storage.public_access import invite_digest
            if args.get('invite_code') is None:raise AuthError('invitation_required')
            try:self.manager.access.check_invite(invite_digest(args['invite_code']),int(self.manager.clock()))
            except ValueError:raise AuthError('invitation_invalid') from None
            if not self.register_ip.take(peer) or not self.register_global.take('register'):raise AuthError('rate_limited')
            return await self.manager.register(**args)
        if op=='login':
            if set(args)!={'account','password'}:raise AuthError('invalid_request')
            name=normalize_account(args['account'])
            if not self.login_ip.take(peer) or not self.login_account.take(name):raise AuthError('rate_limited')
            return await self.manager.login(**args)
        if op=='receipts':
            if set(args)-{'session','after'} or 'session' not in args:raise AuthError('invalid_request')
            _,uid=self.manager._session(args['session'])
            if not self.session_rate.take(uid):raise AuthError('rate_limited')
            return self.manager.access.receipts(uid,args.get('after',0))
        # Standard session operations retain their existing shape and ownership
        # checks; the inherited login/register global bucket is never entered.
        if op not in ('regions','select_region','authorize_game','client_ready','entry_status','cancel_game','logout'):raise AuthError('invalid_request')
        token=args.get('session')
        if token is not None:
            _,uid=self.manager._session(token)
            if not self.session_rate.take(uid):raise AuthError('rate_limited')
        return await super().dispatch(request)
    async def accept_public(self,reader,writer,lease):
        operation='invalid';ok=False
        try:
            async with asyncio.timeout(self.policy.admission_seconds):
                head=await asyncio.wait_for(reader.readexactly(4),self.policy.header_seconds)
                size=struct.unpack('!I',head)[0]
                if not 2<=size<=8192:raise AuthError('invalid_request_size')
                request=strict_json(await reader.readexactly(size))
                if isinstance(request,dict) and request.get('operation') in (*self.event_operations,'capabilities','receipts'):operation=request['operation']
                try:
                    result=await self.dispatch_peer(request,lease.peer);response=dict(ok=True,result=result);ok=True
                except AuthError as exc:response=dict(ok=False,error=str(exc))
                except (ValueError,TypeError,OverflowError):response=dict(ok=False,error='invalid_request')
                except Exception:response=dict(ok=False,error='service_error')
                body=json.dumps(response,separators=(',',':'),ensure_ascii=False).encode()
                if len(body)>65536:raise AuthError('response_budget')
                writer.write(struct.pack('!I',len(body))+body);await asyncio.wait_for(writer.drain(),2)
        except (AuthError,OSError,asyncio.TimeoutError,asyncio.IncompleteReadError):pass
        finally:
            with contextlib.suppress(OSError):self.event_sink(dict(event='auth_request',operation=operation,ok=ok))
