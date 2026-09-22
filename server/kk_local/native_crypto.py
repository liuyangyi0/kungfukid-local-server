"""Own-service record protection (SYSTEM_DESIGN_INFERRED, not old wire crypto).

Fresh 256-bit master keys are delivered only by the authenticated TLS API.
AES-256-GCM keys are separated by channel, direction and connection nonce.
No unauthenticated plaintext is returned, and no plaintext fallback exists.
"""
import asyncio
import hmac
import struct
from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from .wire import ProtocolError

HEADER=struct.Struct('!4sBBH16s16sQI')
MAX_PLAIN=32768
LIMIT=1 << 32  # hard per-key record bound; issue a new grant before exhaustion
SDK,GAME,UDP=1,2,3
DOMAIN=b'KK-native-record-v1\0'

class RecordError(ProtocolError):pass

def inspect(header):
    if len(header)!=HEADER.size:raise RecordError('record header length')
    magic,channel,direction,reserved,sid,cid,seq,size=HEADER.unpack(header)
    if (magic!=b'KKE1' or channel not in (SDK,GAME,UDP) or direction not in (0,1)
            or reserved or seq>=LIMIT or not 1<=size<=MAX_PLAIN):
        raise RecordError('record header rejected')
    return channel,direction,sid,cid,seq,size

class Records:
    def __init__(self,master,sid,cid,channel,*,server):
        if len(master)!=32 or len(sid)!=16 or len(cid)!=16 or channel not in (SDK,GAME,UDP):
            raise ValueError('record configuration')
        self.sid=sid;self.cid=cid;self.channel=channel;self.tx=1 if server else 0
        # HMAC-SHA256 is a PRF KDF with a fresh uniformly random 256-bit key.
        # The public context is fixed width and domain-separated, not password KDF.
        self.keys=[AESGCM(hmac.digest(master,DOMAIN+bytes((channel,d))+sid+cid,'sha256')) for d in (0,1)]
        self.sent=0;self.next=0;self.high=-1;self.seen=0
    def seal(self,plain):
        if not 1<=len(plain)<=MAX_PLAIN or self.sent>=LIMIT:raise RecordError('record send limit')
        seq=self.sent;self.sent+=1
        header=HEADER.pack(b'KKE1',self.channel,self.tx,0,self.sid,self.cid,seq,len(plain))
        return header+self.keys[self.tx].encrypt(bytes(4)+seq.to_bytes(8,'big'),plain,header)
    def open(self,packet):
        channel,direction,sid,cid,seq,size=inspect(packet[:HEADER.size])
        if (channel!=self.channel or direction!=1-self.tx or sid!=self.sid or cid!=self.cid
                or len(packet)!=HEADER.size+size+16):raise RecordError('record binding rejected')
        if self.channel==UDP:
            age=self.high-seq
            if age>=1024 or (age>=0 and self.seen>>age&1):raise RecordError('record replay')
        elif seq!=self.next:raise RecordError('record sequence')
        try:plain=self.keys[direction].decrypt(bytes(4)+seq.to_bytes(8,'big'),packet[HEADER.size:],packet[:HEADER.size])
        except InvalidTag:raise RecordError('record authentication') from None
        # Commit replay state only after successful authentication.
        if self.channel==UDP:
            if seq>self.high:self.seen=(self.seen<<min(seq-self.high,1024))&((1<<1024)-1);self.high=seq
            self.seen|=1<<(self.high-seq)
        else:self.next+=1
        return plain

class RecordReader:
    def __init__(self,raw,records,initial=b''):
        self.raw=raw;self.records=records;self.buffer=bytearray(initial)
    async def read(self,n=65536):
        if n<=0:return b''
        if not self.buffer:
            try:header=await self.raw.readexactly(HEADER.size)
            except asyncio.IncompleteReadError as exc:
                if exc.partial:raise RecordError('truncated encrypted header') from None
                return b''
            *_,size=inspect(header)
            try:body=await self.raw.readexactly(size+16)
            except asyncio.IncompleteReadError:raise RecordError('truncated encrypted body') from None
            self.buffer.extend(self.records.open(header+body))
        out=bytes(self.buffer[:n]);del self.buffer[:n];return out
    async def readexactly(self,n):
        if not 0<=n<=65536:raise RecordError('read limit')
        out=bytearray()
        while len(out)<n:
            data=await self.read(n-len(out))
            if not data:raise asyncio.IncompleteReadError(bytes(out),n)
            out.extend(data)
        return bytes(out)

class RecordWriter:
    def __init__(self,raw,records):self.raw=raw;self.records=records
    def write(self,data):
        for at in range(0,len(data),MAX_PLAIN):self.raw.write(self.records.seal(data[at:at+MAX_PLAIN]))
    async def drain(self):await self.raw.drain()
    def close(self):self.raw.close()
    async def wait_closed(self):await self.raw.wait_closed()
    def get_extra_info(self,*args):return self.raw.get_extra_info(*args)

class NativeProtection:
    def __init__(self,admission):self.admission=admission
    def grant(self,sid):
        grant=next((g for g in self.admission.grants.values() if g.transport_id==sid),None)
        if grant is None:raise RecordError('unknown transport')
        self.admission.validate(grant);return grant
    async def accept(self,reader,writer,channel):
        header=await asyncio.wait_for(reader.readexactly(HEADER.size),10)
        ch,direction,sid,cid,seq,size=inspect(header)
        if ch!=channel or direction or seq or not any(cid):raise RecordError('connection header')
        grant=self.grant(sid);identity=(channel,cid)
        if identity in grant.transport_connections or len(grant.transport_connections)>=32:raise RecordError('connection replay or limit')
        records=Records(grant.transport_key,sid,cid,channel,server=True)
        body=await asyncio.wait_for(reader.readexactly(size+16),10)
        plain=records.open(header+body)
        # No await between recheck and reservation: concurrent replay cannot win.
        self.admission.validate(grant)
        if identity in grant.transport_connections or len(grant.transport_connections)>=32:raise RecordError('connection replay or limit')
        grant.transport_connections.add(identity)
        return RecordReader(reader,records,plain),RecordWriter(writer,records),grant
    def datagram(self,data):
        channel,direction,sid,cid,_,_=inspect(data[:HEADER.size])
        if channel!=UDP or direction or cid!=bytes(16):raise RecordError('UDP binding')
        grant=self.grant(sid)
        if grant.transport_udp is None:grant.transport_udp=Records(grant.transport_key,sid,bytes(16),UDP,server=True)
        return grant,grant.transport_udp.open(data)
