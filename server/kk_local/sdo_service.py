"""Opt-in LOCAL original-window auth integration. Never contacts old services.

SDK cryptography is a compatibility constraint, not modern network security.
Only loopback, exact kernel-attributed SDK/game process pairs are admitted.
Original-window end-to-end qualification is independent from unit tests.
"""
import asyncio
from collections import deque
import contextlib
import hmac
import json
from pathlib import Path
import re
import struct
import time
from urllib.parse import parse_qsl, urlsplit
from cryptography.hazmat.primitives.asymmetric import rsa
from .auth import AuthManager,AuthError,token_digest
from .auth_service import AuthServer
from .native_identity import WindowsNativeVerifier
from .sdo_identity import WindowsSdkVerifier
from .sdo_password import PasswordBridge
from .sdo_wire import response,guid_response,authentication_result
from .service import Service,ReadyFile
from .store import Store
from .maps import MapCatalog


class SdoHttpServer:
    def __init__(self, auth, key, verifier, *, port=18082, region=1, event_sink=None):
        self.auth=auth;self.bridge=PasswordBridge(auth,key);self.verifier=verifier
        self.port=port;self.region=region;self.server=None;self.tasks=set();self.writers=set()
        self.recent=deque();self.event_sink=event_sink or (lambda event:None)

    async def start(self):
        self.server=await asyncio.start_server(self.accept,'127.0.0.1',self.port,limit=8192)
        self.port=self.server.sockets[0].getsockname()[1];return self

    async def close(self):
        if self.server:self.server.close();await self.server.wait_closed()
        for w in list(self.writers):w.close()
        pending=list(self.tasks)
        for t in pending:t.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)

    async def dispatch(self, path, fields, pair):
        self.verifier.recheck(pair)
        if path=='/authen/getPromotionInfo.json':
            # Native 10012F00 -> sdologin 498390: an empty promotionUrl does
            # not enqueue the promotion window. No legacy marketing service.
            # PROVISIONAL local policy, and never a new authentication grant.
            binding=self.auth.native_binding(pair[1],self.region)
            if not hmac.compare_digest(token_digest(fields.get('tgt')),binding['session_digest']):
                raise AuthError('invalid_session')
            return response(0,{'promotionUrl':'','failReason':''}),None
        if path=='/authen/checkAccountType.json':
            # SdoBaseClient 1000FED0 -> CAuthenManager account-type callback.
            # Local policy describes password-only accounts, never grants login.
            name=fields.get('inputUserId','')
            if not re.fullmatch(r'[A-Za-z0-9]{3,20}',name):raise AuthError('invalid_credentials')
            return response(0,{'type':'1','level':'0','existing':'1','mobileMask':'',
                'fromWoa':'0','hasPwdLoginRecord':'1','recommendLoginType':'0',
                'hasCheckCodeLoginRecord':'0','ptMask':'','failReason':''}),None
        if path=='/authen/getGuid.json':
            if fields.get('generateDynamicKey')!='1' or fields.get('key',''):
                raise AuthError('unsupported_auth_flow')
            issued=self.bridge.issue(pair)
            return guid_response(issued['guid'],issued['dynamicKey']),None
        if path!='/authen/staticLogin.json':raise AuthError('unsupported_auth_flow')
        if fields.get('encryptFlag')!='1' or fields.get('checkCodeFlag')!='1' or fields.get('accountDomain')!='1' or fields.get('autoLoginFlag','0')!='0':
            raise AuthError('unsupported_auth_flow')
        granted=await self.bridge.authenticate(client_identity=pair,guid=fields.get('guid'),
            account_cipher=fields.get('inputUserId'),password_cipher=fields.get('password'))
        try:
            self.verifier.recheck(pair)
            selected=self.auth.select_region(granted['session'],self.region)
            self.auth.bind_native_client(selected['ticket'],granted['uid'],self.region,pair[1].pid)
        except BaseException:
            self.auth.logout(granted['session']);raise
        # Reuse the authenticated session as SDK ticket; not the old LS envelope.
        token=granted['session']
        return authentication_result(authenticated_uid=granted['uid'],ticket=token,session_id=token),token

    async def accept(self, reader, writer):
        if len(self.tasks)>=8:writer.close();return
        task=asyncio.current_task();self.tasks.add(task);self.writers.add(writer)
        operation='rejected';ok=False;cookie=None;body=authentication_result(authenticated_uid=None,ticket='',session_id='')
        try:
            raw=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),5)
            if len(raw)>8192:raise ValueError('large headers')
            lines=raw.decode('ascii').split('\r\n');method,target,version=lines[0].split(' ')
            if method!='GET' or version not in ('HTTP/1.0','HTTP/1.1') or len(target)>4096:raise ValueError('invalid request')
            headers={}
            for line in lines[1:]:
                if not line:continue
                k,v=line.split(':',1);k=k.lower()
                if k in headers:raise ValueError('duplicate header')
                headers[k]=v.strip()
            if headers.get('content-length','0')!='0' or 'transfer-encoding' in headers or 'origin' in headers:raise ValueError('body or browser origin')
            if headers.get('host') not in (f'127.0.0.1:{self.port}','127.0.0.1'):raise ValueError('host mismatch')
            parsed=urlsplit(target)
            if parsed.scheme or parsed.netloc or parsed.fragment:raise ValueError('absolute target')
            if re.search(r'%(?![0-9a-fA-F]{2})',parsed.query):raise ValueError('bad escape')
            pairs=parse_qsl(parsed.query,keep_blank_values=True,max_num_fields=32,encoding='ascii',errors='strict')
            fields=dict(pairs)
            if len(fields)!=len(pairs) or any('\0' in k+v for k,v in pairs):raise ValueError('duplicate/nul query')
            pair=self.verifier.peer(writer.get_extra_info('peername'),writer.get_extra_info('sockname'))
            now=time.monotonic()
            while self.recent and self.recent[0]<now-60:self.recent.popleft()
            if len(self.recent)>=30:raise AuthError('rate_limited')
            self.recent.append(now)
            path=parsed.path
            if path in ('authen/getGuid.json','authen/staticLogin.json','authen/checkAccountType.json','authen/getPromotionInfo.json'):path='/'+path
            operation={'/authen/getGuid.json':'guid','/authen/staticLogin.json':'password','/authen/checkAccountType.json':'account_type','/authen/getPromotionInfo.json':'promotion'}.get(path,'unsupported')
            body,cookie=await self.dispatch(path,fields,pair);ok=True
        except (ValueError,AuthError,UnicodeError,asyncio.TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):
            pass
        except Exception:
            # Log no exception text: it could contain attacker-controlled input.
            operation='internal_error'
        try:
            headers=b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\n'
            if cookie:headers+=f'Set-Cookie: CASTGC={cookie}; Path=/; HttpOnly; SameSite=Strict\r\n'.encode()
            writer.write(headers+f'Content-Length: {len(body)}\r\n\r\n'.encode()+body)
            await asyncio.wait_for(writer.drain(),3)
        except (OSError,asyncio.TimeoutError):pass
        finally:
            self.event_sink(dict(event='sdo_http',operation=operation,ok=ok))
            writer.close();self.tasks.discard(task);self.writers.discard(writer)
            with contextlib.suppress(OSError,asyncio.TimeoutError):await asyncio.wait_for(writer.wait_closed(),2)


async def run(args):
    from .app.lifecycle import EventLog,own_services,start_services
    root=Path(args.client_root).resolve(strict=True);runtime=Path(args.runtime).resolve();runtime.mkdir(exist_ok=False)
    map_catalog=MapCatalog.from_client(root)
    (runtime/'map-admission.json').write_text(json.dumps(map_catalog.summary(),ensure_ascii=False,indent=2),encoding='utf8')
    key=rsa.generate_private_key(public_exponent=3,key_size=1024)
    public=key.public_key().public_numbers()
    with (runtime/'public-key.bin').open('xb') as f:f.write(struct.pack('<H',1024)+public.n.to_bytes(128,'big')+public.e.to_bytes(128,'big'))
    async with contextlib.AsyncExitStack() as stack:
        store=Store(args.database);stack.callback(store.close)
        emit=stack.enter_context(EventLog(runtime/'events.jsonl',exclusive=True,timestamps=True,ensure_ascii=True))
        game_identity=WindowsNativeVerifier(root/'gfxz-lab.exe')
        auth=AuthManager(store,[dict(id=1,name='Local',host='127.0.0.1',game_port=args.game_port)],native_verifier=game_identity)
        stack.callback(auth.close)
        verifier=WindowsSdkVerifier(root/'sdo/sdologin/sdologin.exe',game_identity)
        http=SdoHttpServer(auth,key,verifier,port=args.http_port,event_sink=emit)
        api=AuthServer(auth,port=args.api_port,event_sink=emit)
        game=Service(store,ReadyFile(root/'kk-roleprop-ready.txt'),native_auth=auth,login_port=args.login_port,
                     game_port=args.game_port,p2p_port=args.game_port,event_sink=emit,map_catalog=map_catalog,
                     query_probe=getattr(args,'query_probe',False))
        own_services(stack,[http,api,game])
        await start_services([game,http,api])
        (runtime/'ready.json').write_text(json.dumps(dict(status='ready',http_port=http.port,api_port=api.port,login_port=game.login_port,game_port=game.game_port,private_key_persisted=False)),encoding='utf8')
        await asyncio.Event().wait()


def main(argv=None):
    from .app.cli import run_mode
    run_mode('sdo',argv,run)


if __name__=='__main__':main()
