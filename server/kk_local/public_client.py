"""Independent reference peer. Standard sockets/TLS only; no SDK/DLL/VM.

Never automatically resend an economic game frame after ambiguous delivery.
"""
import argparse
import asyncio
import getpass
import json
import secrets
import socket
import ssl
import struct
import sys
from .native_crypto import Records,RecordReader,RecordWriter,SDK,GAME,UDP
from .wire import Message,GameDecoder,encode_game,encode_login,read_login,sdp_header

class ReferenceClient:
    def __init__(self,host,auth_port,*,context,server_name='localhost',local_ip=None):
        self.host=host;self.auth_port=auth_port;self.context=context;self.server_name=server_name;self.local_ip=local_ip
        self.writers=[];self.game=None;self.pending=[];self.udp=None;self.grant=None;self.session=None;self.received_udp=0
        self.pump=None;self.changed=asyncio.Condition();self.failure=None
    async def rpc(self,operation,**arguments):
        options=dict(ssl=self.context,server_hostname=self.server_name)
        if self.local_ip:options['local_addr']=(self.local_ip,0)
        r,w=await asyncio.wait_for(asyncio.open_connection(self.host,self.auth_port,**options),10)
        try:
            data=json.dumps(dict(schema='kk-local-auth-v1',operation=operation,arguments=arguments)).encode()
            w.write(struct.pack('!I',len(data))+data);await w.drain()
            size=struct.unpack('!I',await asyncio.wait_for(r.readexactly(4),15))[0]
            if not 2<=size<=65536:raise ValueError('response size')
            result=json.loads(await r.readexactly(size))
            if not result['ok']:raise ValueError(result['error'])
            return result['result']
        finally:w.close();await w.wait_closed()
    def records(self,channel):
        g=self.grant
        return Records(bytes.fromhex(g['transport_key']),bytes.fromhex(g['transport_id']),bytes(16) if channel==UDP else secrets.token_bytes(16),channel,server=False)
    async def connection(self,channel):
        port=self.grant['sdk_port'] if channel==SDK else self.grant['game_port']
        options={'local_addr':(self.local_ip,0)} if self.local_ip else {}
        r,w=await asyncio.open_connection(self.host,port,**options);self.writers.append(w);records=self.records(channel)
        return RecordReader(r,records),RecordWriter(w,records),GameDecoder()
    def hello(self,ident):
        g=self.grant;p=bytearray(96);struct.pack_into('<Q',p,0,g['uid']);p[16]=32;p[17:49]=bytes.fromhex(g['game_credential'])
        struct.pack_into('<I',p,49,594);name=self.account.encode('ascii');p[73:73+len(name)]=name
        return Message(ident,bytes(p))
    async def read_until(self,ident,timeout=15):
        async with asyncio.timeout(timeout):
            while True:
                for i,message in enumerate(self.pending):
                    if message.id==ident:return self.pending.pop(i)
                if self.failure:raise ConnectionError(self.failure)
                if self.pump:
                    async with self.changed:await self.changed.wait()
                    continue
                data=await self.game[0].read(65536)
                if not data:raise ConnectionError('game closed')
                self.pending.extend(self.game[2].feed(data))
                if len(self.pending)>4096:raise ValueError('reference receive budget')
    def start_pump(self):
        async def pump():
            try:
                while True:
                    data=await self.game[0].read(65536)
                    if not data:raise ConnectionError('game closed')
                    messages=self.game[2].feed(data)
                    async with self.changed:
                        self.pending.extend(m for m in messages if m.id!=0)
                        if len(self.pending)>4096:raise ValueError('reference receive budget')
                        self.changed.notify_all()
            except (OSError,ValueError,ConnectionError) as exc:
                self.failure=type(exc).__name__
                async with self.changed:self.changed.notify_all()
        self.pump=asyncio.create_task(pump())
    async def send(self,message):self.game[1].write(encode_game(message));await self.game[1].drain()
    async def login(self,account,password):
        self.account=account.lower();auth=await self.rpc('login',account=account,password=password);self.session=auth['session']
        ticket=await self.rpc('select_region',session=self.session,region_id=1)
        self.grant=await self.rpc('authorize_game',ticket=ticket['ticket'],region_id=1)
        sdk=await self.connection(SDK);body=bytearray(encode_login(Message(1001,b'reference-peer')));body[6]=1
        sdk[1].write(b'KKS1'+bytes.fromhex(self.grant['sdk_credential'])+body);await sdk[1].drain();await read_login(sdk[0])
        sdk[1].write(encode_login(Message(1011)));await sdk[1].drain();await read_login(sdk[0])
        await self.rpc('client_ready',session=self.session,game_credential=self.grant['game_credential'])
        self.game=await self.connection(GAME);await self.send(self.hello(1010));await self.read_until(1151)
        await self.send(Message(3320,struct.pack('<I',1)));await self.read_until(1201);self.game[1].close();self.pending=[]
        self.game=await self.connection(GAME);await self.send(self.hello(2010));await self.read_until(2030)
        await self.send(Message(2250,struct.pack('<II',1,10)))
        await self.bind_udp()
        return self.grant['uid']
    async def bind_udp(self):
        self.udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);self.udp.setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024);self.udp.bind((self.local_ip or '0.0.0.0',0));self.udp.setblocking(False)
        self.udp_records=self.records(UDP);g=self.grant;name=self.account.encode()
        body=struct.pack('>H',len(name))+name+bytes(10)+b'KKN1:'+g['udp_credential'].encode()+bytes(60)
        await self.send_udp(self.sdp(1001,body))
        data=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(self.udp,65536),10)
        ident,session,_,_,body,_=sdp_header(self.udp_records.open(data))
        if ident!=1002:raise ValueError('UDP handshake')
        _,self.peer_id,self.peer_session=struct.unpack_from('>III',body)
        await self.send_udp(self.sdp(1013,bytes(4)))
        response=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(self.udp,65536),10)
        if sdp_header(self.udp_records.open(response))[0]!=1014:raise ValueError('UDP reachability confirmation')
        await self.send(Message(1156,struct.pack('<QI',g['uid'],self.peer_id)))
    def sdp(self,ident,body,targets=()):
        extra=struct.pack('<'+len(targets)*'I',*targets)
        return struct.pack('<HHIIIIHBB',1,ident,getattr(self,'peer_session',0),0,getattr(self,'peer_id',0),0,0,0,len(extra))+extra+body
    async def send_udp(self,data):
        wire=self.udp_records.seal(data);await asyncio.get_running_loop().sock_sendto(self.udp,wire,(self.host,self.grant['udp_port']))
    async def close(self):
        if self.pump:self.pump.cancel();await asyncio.gather(self.pump,return_exceptions=True);self.pump=None
        if self.udp:self.udp.close();self.udp=None
        for w in self.writers:w.close()
        for w in self.writers:
            try:await w.wait_closed()
            except (OSError,ConnectionError):pass
        self.writers=[]

async def command(args):
    context=ssl.create_default_context(cafile=args.ca)
    client=ReferenceClient(args.host,args.port,context=context,server_name=args.server_name)
    if args.action=='register':
        password=getpass.getpass('Password: ');invite=getpass.getpass('Invitation: ')
        result=await client.rpc('register',account=args.account,password=password,invite_code=invite)
        print(json.dumps(dict(uid=result['uid'],registered=True)))
    else:
        try:uid=await client.login(args.account,getpass.getpass('Password: '));print(json.dumps(dict(uid=uid,stage='lobby')))
        finally:await client.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('action',choices=('register','login'));p.add_argument('--host',default='127.0.0.1');p.add_argument('--port',type=int,default=17999)
    p.add_argument('--server-name',required=True);p.add_argument('--ca');p.add_argument('--account',required=True)
    args=p.parse_args()
    if not sys.stdin.isatty():p.error('interactive terminal required; do not pipe passwords or invitations')
    asyncio.run(command(args))
if __name__=='__main__':main()
