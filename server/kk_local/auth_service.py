"""Length-prefixed local authentication API plus the guarded native game service.

Wire: BE u32 length, UTF-8 JSON. One request per connection. It is deliberately
not HTTP and cannot be submitted by an ordinary cross-origin web form.
"""
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
    event_operations=('register','login','regions','select_region','bind_client','status','logout')
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
            if isinstance(request,dict) and request.get('operation') in self.event_operations:
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
    from .app.lifecycle import EventLog,own_services,start_services
    root=Path(args.client_root).resolve(strict=True)
    from .maps import MapCatalog
    map_catalog=MapCatalog.from_client(root)
    verifier=WindowsNativeVerifier(root/'gfxz-lab.exe')
    async with contextlib.AsyncExitStack() as stack:
        store=Store(args.database);stack.callback(store.close)
        emit=stack.enter_context(EventLog(args.events,timestamps=True))
        #No implicit KKLocal account or password bypass in authenticated mode.
        manager=AuthManager(store,[dict(id=1,name='本地区服',host='127.0.0.1',game_port=args.game_port)],native_verifier=verifier)
        stack.callback(manager.close)
        game=Service(store,ReadyFile(args.role_ready_file),native_auth=manager,
                     login_port=args.login_port,game_port=args.game_port,p2p_port=args.game_port,event_sink=emit,map_catalog=map_catalog)
        auth=AuthServer(manager,port=args.auth_port,event_sink=emit)
        own_services(stack,[auth,game])
        await start_services([game,auth])
        print(json.dumps(dict(status='listening',authentication='password-and-local-process-binding',
                              auth_port=auth.port,login_port=game.login_port,game_port=game.game_port)),flush=True)
        await asyncio.Event().wait()


def main(argv=None):
    from .app.cli import run_mode
    run_mode('auth',argv,run)


if __name__=='__main__':main()
