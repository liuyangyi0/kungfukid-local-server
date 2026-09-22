"""Acquire a bounded lease immediately after accept, BEFORE TLS work."""
import asyncio
import contextlib
import socket
from .auth import AuthError

class BoundedListener:
    def __init__(self,host,port,handler,budget,*,ssl_context=None,tls_seconds=5,stream_limit=32768):
        self.host=host;self.port=port;self.handler=handler;self.budget=budget;self.ssl_context=ssl_context
        self.tls_seconds=tls_seconds;self.stream_limit=stream_limit;self.socket=None;self.accept_task=None
        self.tasks=set();self.transports=set();self.sockets=[];self.errors=0
    async def start(self):
        s=socket.socket(socket.AF_INET,socket.SOCK_STREAM)
        try:
            s.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1);s.bind((self.host,self.port));s.listen(128);s.setblocking(False)
        except BaseException:s.close();raise
        self.socket=s;self.sockets=[s];self.port=s.getsockname()[1];self.accept_task=asyncio.create_task(self.accept())
        return self
    async def accept(self):
        loop=asyncio.get_running_loop()
        while True:
            s,peer=await loop.sock_accept(self.socket)
            try:lease=self.budget.acquire(peer[0])
            except AuthError:s.close();continue
            task=asyncio.create_task(self.attach(s,lease));self.tasks.add(task)
            def finished(done):
                self.tasks.discard(done)
                if not done.cancelled() and done.exception() is not None:self.errors+=1
            task.add_done_callback(finished)
    async def attach(self,s,lease):
        connected=False;transport=None;handler_task=None
        try:
            reader=asyncio.StreamReader(limit=self.stream_limit)
            def connected_cb(r,w):
                nonlocal connected,handler_task
                connected=True
                async def handle():
                    try:await self.handler(r,w,lease)
                    finally:
                        lease.release();w.close()
                        with contextlib.suppress(Exception):await asyncio.wait_for(w.wait_closed(),2)
                handler_task=asyncio.create_task(handle())
            protocol=asyncio.StreamReaderProtocol(reader,connected_cb)
            options=dict(ssl=self.ssl_context)
            if self.ssl_context:options['ssl_handshake_timeout']=self.tls_seconds;options['ssl_shutdown_timeout']=2
            transport,_=await asyncio.get_running_loop().connect_accepted_socket(lambda:protocol,s,**options)
            self.transports.add(transport)
            if handler_task:await handler_task
        except (OSError,ConnectionError,asyncio.TimeoutError):pass
        finally:
            if handler_task and not handler_task.done():handler_task.cancel();await asyncio.gather(handler_task,return_exceptions=True)
            lease.release()
            if transport:self.transports.discard(transport);transport.close()
            elif not connected:s.close()
    def close(self):
        if self.accept_task:self.accept_task.cancel()
        if self.socket:self.socket.close()
        for t in tuple(self.transports):t.close()
        for task in tuple(self.tasks):task.cancel()
    async def wait_closed(self):
        tasks=([self.accept_task] if self.accept_task else [])+list(self.tasks)
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
