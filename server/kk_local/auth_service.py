"""Length-prefixed local authentication API plus the guarded native game service.

Wire: BE u32 length, UTF-8 JSON. One request per connection. It is deliberately
not HTTP and cannot be submitted by an ordinary cross-origin web form.
"""
import argparse
import asyncio
from collections import deque
import contextlib
import json
from pathlib import Path
import struct
import time

from .auth import AuthManager, AuthError
from .native_identity import WindowsNativeVerifier
from .service import Service, ReadyFile
from .store import Store


class AuthServer:
    def __init__(self, manager, *, port=7999, event_sink=None):
        self.manager=manager
        self.port=port
        self.server=None
        self.tasks=set()
        self.writers=set()
        self.recent_sensitive=deque()
        self.event_sink=event_sink or (lambda row:None)

    async def start(self):
        self.server=await asyncio.start_server(self._accept,'127.0.0.1',self.port,limit=8192)
        self.port=self.server.sockets[0].getsockname()[1]
        return self

    async def close(self):
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        for writer in list(self.writers): writer.close()
        tasks=list(self.tasks)
        for task in tasks: task.cancel()
        if tasks: await asyncio.gather(*tasks,return_exceptions=True)

    async def dispatch(self, request):
        if not isinstance(request,dict) or set(request)!={'schema','operation','arguments'} or request['schema']!='kk-local-auth-v1':
            raise AuthError('invalid_request')
        op=request['operation']; args=request['arguments']
        fields={'register':({'account','password'},{'nickname'}),
                'login':({'account','password'},set()),'regions':({'session'},set()),
                'select_region':({'session','region_id'},set()),
                'bind_client':({'ticket','uid','region_id','pid'},set()),
                'status':({'session','pid'},set()),'logout':({'session'},set())}
        if not isinstance(op,str) or op not in fields or not isinstance(args,dict):
            raise AuthError('invalid_request')
        required,optional=fields[op]
        if not required<=set(args) or set(args)-required-optional:
            raise AuthError('invalid_request')
        if op in ('register','login'):
            now=time.monotonic()
            while self.recent_sensitive and self.recent_sensitive[0]<=now-60:
                self.recent_sensitive.popleft()
            if len(self.recent_sensitive)>=20:
                raise AuthError('rate_limited')
            self.recent_sensitive.append(now)
        if op=='register': return await self.manager.register(**args)
        if op=='login': return await self.manager.login(**args)
        if op=='regions': return self.manager.list_regions(args['session'])
        if op=='select_region': return self.manager.select_region(args['session'],args['region_id'])
        if op=='bind_client': return self.manager.bind_native_client(**args)
        if op=='status': return self.manager.native_status(args['session'],args['pid'])
        self.manager.logout(args['session'])
        return dict(logged_out=True)

    async def _accept(self, reader, writer):
        if len(self.tasks)>=8 or writer.get_extra_info('peername')[0]!='127.0.0.1':
            writer.close()
            return
        task=asyncio.current_task()
        self.tasks.add(task); self.writers.add(writer)
        operation='invalid'
        try:
            header=await asyncio.wait_for(reader.readexactly(4),5)
            n=struct.unpack('!I',header)[0]
            if not 2<=n<=8192: raise AuthError('invalid_request_size')
            raw=await asyncio.wait_for(reader.readexactly(n),5)
            try:
                request=json.loads(raw.decode('utf-8'))
            except (UnicodeError,ValueError,RecursionError):
                raise AuthError('invalid_request') from None
            if isinstance(request,dict) and request.get('operation') in ('register','login','regions','select_region','bind_client','status','logout'):
                operation=request['operation']
            try:
                result=await self.dispatch(request)
                response=dict(ok=True,result=result)
            except AuthError as exc:
                response=dict(ok=False,error=str(exc))
            except (ValueError,TypeError,OverflowError):
                response=dict(ok=False,error='invalid_request')
            except Exception:
                response=dict(ok=False,error='service_error')
            encoded=json.dumps(response,ensure_ascii=False,separators=(',',':')).encode('utf-8')
            writer.write(struct.pack('!I',len(encoded))+encoded)
            await asyncio.wait_for(writer.drain(),3)
            try:
                self.event_sink(dict(event='auth_request',operation=operation,ok=response['ok']))
            except OSError: pass
        except (AuthError,asyncio.IncompleteReadError,asyncio.TimeoutError,ConnectionError,OSError):
            pass
        finally:
            self.tasks.discard(task); self.writers.discard(writer)
            writer.close()
            with contextlib.suppress(OSError,asyncio.TimeoutError):
                await asyncio.wait_for(writer.wait_closed(),2)


async def run(args):
    root=Path(args.client_root).resolve(strict=True)
    from .maps import MapCatalog
    map_catalog=MapCatalog.from_client(root)
    verifier=WindowsNativeVerifier(root/'gfxz-lab.exe')
    store=Store(args.database)
    # Do not seed an automatically accessible KKLocal account. Existing accounts
    # stay intact and need an explicitly provisioned password.
    manager=AuthManager(store,[dict(id=1,name='本地区服',host='127.0.0.1',game_port=args.game_port)],native_verifier=verifier)
    Path(args.events).parent.mkdir(parents=True,exist_ok=True)
    log=open(args.events,'a',encoding='utf-8',buffering=1)
    def emit(row):
        log.write(json.dumps({'timestamp':time.time(),**row},ensure_ascii=False)+'\n')
    game=Service(store,ReadyFile(args.role_ready_file),native_auth=manager,
                 login_port=args.login_port,game_port=args.game_port,p2p_port=args.game_port,event_sink=emit,map_catalog=map_catalog)
    auth=AuthServer(manager,port=args.auth_port,event_sink=emit)
    try:
        await game.start()
        await auth.start()
        print(json.dumps(dict(status='listening',authentication='password-and-local-process-binding',
                              auth_port=auth.port,login_port=game.login_port,game_port=game.game_port)),flush=True)
        await asyncio.Event().wait()
    finally:
        await auth.close(); await game.close()
        manager.close(); store.close(); log.close()


def main():
    parser=argparse.ArgumentParser(description='Local password authentication; does not contact SDO')
    parser.add_argument('--database',required=True)
    parser.add_argument('--client-root',required=True)
    parser.add_argument('--role-ready-file',required=True)
    parser.add_argument('--events',required=True)
    parser.add_argument('--auth-port',type=int,default=7999)
    parser.add_argument('--login-port',type=int,default=8000)
    parser.add_argument('--game-port',type=int,default=8001)
    args=parser.parse_args()
    if len({args.auth_port,args.login_port,args.game_port})!=3 or not all(1<=p<=65535 for p in (args.auth_port,args.login_port,args.game_port)):
        parser.error('three distinct valid TCP ports required')
    try: asyncio.run(run(args))
    except KeyboardInterrupt: pass


if __name__=='__main__': main()
