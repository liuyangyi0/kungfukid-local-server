"""Run owned i686 model against Python crypto over real loopback sockets only."""
import argparse
import asyncio
import pathlib
import sys
sys.path.insert(0,str(pathlib.Path(__file__).resolve().parents[2]))
from server.kk_local.native_crypto import Records,HEADER,inspect,GAME,UDP

async def main(model):
    failures=[];observed={'tcp':0,'udp':0};tasks=set()
    async def accept(reader,writer):
        tasks.add(asyncio.current_task());state=None
        try:
            while True:
                try:head=await asyncio.wait_for(reader.readexactly(HEADER.size),10)
                except asyncio.IncompleteReadError as exc:
                    assert not exc.partial;break
                ch,direction,sid,cid,_,n=inspect(head);assert ch==GAME and direction==0 and sid==bytes([1])*16
                if state is None:state=Records(bytes([2])*32,sid,cid,GAME,server=True)
                packet=head+await reader.readexactly(n+16);plain=state.open(packet)
                observed['tcp']+=len(plain)
                encrypted=state.seal(plain)
                # Deliberately fragmented writes exercise partial ciphertext.
                for at in range(0,len(encrypted),701):writer.write(encrypted[at:at+701]);await writer.drain();await asyncio.sleep(0)
        except Exception as exc:failures.append(type(exc).__name__)
        finally:writer.close();tasks.discard(asyncio.current_task())
    class Echo(asyncio.DatagramProtocol):
        def connection_made(self,t):self.t=t;self.r=Records(bytes([2])*32,bytes([1])*16,bytes(16),UDP,server=True)
        def datagram_received(self,data,peer):
            try:plain=self.r.open(data);assert plain==b'udp';observed['udp']+=1;self.t.sendto(self.r.seal(plain),peer)
            except Exception as exc:failures.append(type(exc).__name__)
    server=await asyncio.start_server(accept,'127.0.0.1',0)
    udp,_=await asyncio.get_running_loop().create_datagram_endpoint(Echo,local_addr=('127.0.0.1',0))
    child=None
    try:
        child=await asyncio.create_subprocess_exec(str(pathlib.Path(model).resolve(strict=True)),'--encrypted-echo',str(server.sockets[0].getsockname()[1]),str(udp.get_extra_info('sockname')[1]),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=0x08000000)
        out,err=await asyncio.wait_for(child.communicate(),20)
        assert child.returncode==0,(child.returncode,err.decode(errors='replace'))
        assert not failures,failures
        assert observed=={'tcp':100000,'udp':1},observed
        print(out.decode().strip())
    finally:
        if child is not None and child.returncode is None:child.kill();await child.wait()
        server.close();await server.wait_closed();udp.close()
        for task in tuple(tasks):task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--model',required=True);asyncio.run(main(p.parse_args().model))
