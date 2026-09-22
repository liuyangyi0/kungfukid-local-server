"""Owned-process, loopback-only 100-player benchmark. Never targets the Internet.

Creates a new temporary account database and certificates. No original assets,
real accounts, VM or client DLL. A separate server process gets resource limits.
"""
import argparse
import asyncio
import contextlib
import ctypes
import json
import os
from pathlib import Path
import secrets
import socket
import ssl
import struct
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from server.kk_local.app.public import PublicRuntime
from server.kk_local.public_policy import PublicPolicy
from server.kk_local.public_client import ReferenceClient
from server.kk_local.public_auth import PublicAuthManager
from server.kk_local.store import Store
from server.kk_local.wire import Message,encode_game,sdp_header

_job=None
def constrain():
    global _job
    result={'cpu_limit':False,'memory_limit':False,'memory_bytes':2*1024**3}
    try:
        if os.name=='nt':
            from ctypes import wintypes as w
            k=ctypes.WinDLL('kernel32',use_last_error=True);k.GetCurrentProcess.restype=w.HANDLE
            handle=k.GetCurrentProcess();process=ctypes.c_size_t();system=ctypes.c_size_t()
            k.GetProcessAffinityMask.argtypes=[w.HANDLE,ctypes.POINTER(ctypes.c_size_t),ctypes.POINTER(ctypes.c_size_t)]
            k.SetProcessAffinityMask.argtypes=[w.HANDLE,ctypes.c_size_t]
            if not k.GetProcessAffinityMask(handle,ctypes.byref(process),ctypes.byref(system)):raise OSError()
            bits=[1<<i for i in range(64) if process.value&(1<<i)][:2]
            result['cpu_limit']=bool(k.SetProcessAffinityMask(handle,sum(bits))) and len(bits)==2;result['logical_cpus']=len(bits)
            class Basic(ctypes.Structure):_fields_=[('per_process',ctypes.c_int64),('per_job',ctypes.c_int64),('flags',w.DWORD),('minimum',ctypes.c_size_t),('maximum',ctypes.c_size_t),('active',w.DWORD),('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
            class IO(ctypes.Structure):_fields_=[(n,ctypes.c_uint64) for n in ('a','b','c','d','e','f')]
            class Extended(ctypes.Structure):_fields_=[('basic',Basic),('io',IO),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
            k.CreateJobObjectW.restype=w.HANDLE;_job=k.CreateJobObjectW(None,None)
            k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD];k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
            info=Extended();info.basic.flags=0x100;info.process_memory=2*1024**3
            result['memory_limit']=bool(k.SetInformationJobObject(_job,9,ctypes.byref(info),ctypes.sizeof(info)) and k.AssignProcessToJobObject(_job,handle))
        else:
            import resource
            cpus=sorted(os.sched_getaffinity(0))[:2];os.sched_setaffinity(0,cpus);result['cpu_limit']=len(cpus)==2;result['logical_cpus']=len(cpus)
            resource.setrlimit(resource.RLIMIT_AS,(2*1024**3,2*1024**3));result['memory_limit']=True
    except (OSError,AttributeError):result['error']='resource_constraint_unavailable'
    return result

def rss():
    if os.name!='nt':return int(Path('/proc/self/statm').read_text().split()[1])*os.sysconf('SC_PAGE_SIZE')
    from ctypes import wintypes as w
    class Counters(ctypes.Structure):_fields_=[('cb',w.DWORD),('faults',w.DWORD)]+[(n,ctypes.c_size_t) for n in ('peak','rss','quota_peak','quota','paged_peak','paged','pagefile','peak_pagefile')]
    k=ctypes.WinDLL('kernel32');k.GetCurrentProcess.restype=w.HANDLE
    p=ctypes.WinDLL('psapi');p.GetProcessMemoryInfo.argtypes=[w.HANDLE,ctypes.c_void_p,w.DWORD]
    data=Counters();data.cb=ctypes.sizeof(data)
    if not p.GetProcessMemoryInfo(k.GetCurrentProcess(),ctypes.byref(data),data.cb):return 0
    return data.rss

async def worker(spec_path):
    spec=json.loads(Path(spec_path).read_text());limits=constrain();root=Path(spec['root'])
    args=SimpleNamespace(database=str(root/'public.sqlite3'),events=str(root/'events.jsonl'),auth_certificate=str(root/'cert.pem'),auth_key=str(root/'key.pem'),listen_host='127.0.0.1',advertised_host='127.0.0.1',game_port=0,sdk_port=0,udp_port=0,auth_port=0,health_port=0,public_policy=PublicPolicy())
    runtime=await PublicRuntime(args).start();samples=[];started=time.monotonic();cpu=time.process_time()
    print(json.dumps(dict(**runtime.status(),constraints=limits)),flush=True)
    async def sample():
        while True:
            await asyncio.sleep(10)
            samples.append(dict(elapsed=time.monotonic()-started,rss=rss(),cpu_seconds=time.process_time()-cpu,online=len(runtime.admission.by_uid),incoming=runtime.game.incoming.used,outgoing=runtime.game.outgoing.used,**runtime.metrics.snapshot()))
    monitor=asyncio.create_task(sample());phases={}
    try:
        while True:
            command=(await asyncio.to_thread(sys.stdin.readline)).strip()
            if command=='steady':
                phases['startup']=runtime.metrics.snapshot();runtime.metrics.histograms.clear()
                samples.clear();started=time.monotonic();cpu=time.process_time()
                runtime.metrics.udp_rx_bytes=runtime.metrics.udp_tx_bytes=0
            elif command=='attack':phases['steady']=runtime.metrics.snapshot();runtime.metrics.histograms.clear()
            else:break
    finally:
        monitor.cancel();await asyncio.gather(monitor,return_exceptions=True)
        report=dict(constraints=limits,phases=phases,samples=samples,final=runtime.metrics.snapshot(),incoming_peak=runtime.game.incoming.peak,outgoing_peak=runtime.game.outgoing.peak)
        await runtime.close()
        report['leases_after_close']=dict(pending=runtime.game.connections.pending,active=runtime.game.connections.active)
        (root/'worker-report.json').write_text(json.dumps(report,indent=2))

async def run(args):
    root=Path(args.output).resolve()
    if root.exists():raise ValueError('output must be a new directory')
    root.mkdir(parents=True)
    # Only owned synthetic fixtures; production server imports no test modules.
    from server.tests.test_public_server import prepare
    setup=prepare(root);store=Store(setup.database,public=True)
    manager=PublicAuthManager(store,[dict(id=1,name='Load',host='127.0.0.1',game_port=18001)],policy=PublicPolicy())
    accounts=[]
    try:
        for i in range(args.players):
            name=f'Load{i:04d}';password=secrets.token_hex(16)
            with store.transaction():invite=manager.access.create_invite(int(time.time()))
            registered=await manager.register(name,password,invite_code=invite);accounts.append((name,password))
            if args.varied_inventory:
                uid=registered['uid'];template=bytearray(store.snapshot(uid)[3][:68]);struct.pack_into('<H',template,17,0)
                with store.transaction():
                    for _ in range((0,100,1000)[i%3]):
                        instance=store._allocate_inventory_instance();struct.pack_into('<I',template,0,instance);store.inventory.insert(uid,instance,bytes(template))
    finally:manager.close();store.close()
    spec=root/'worker-spec.json';spec.write_text(json.dumps(dict(root=str(root))))
    child=await asyncio.create_subprocess_exec(sys.executable,'-m','server.tools.public_benchmark','--worker',str(spec),stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,creationflags=0x08000000 if os.name=='nt' else 0)
    error_task=asyncio.create_task(child.stderr.read());clients=[];tasks=[];failures=[];sent=0;received=0;heartbeat=None;flood=None
    try:
        status=json.loads(await asyncio.wait_for(child.stdout.readline(),30));print(json.dumps(dict(stage='server_started',**status)),flush=True)
        context=ssl.create_default_context(cafile=str(root/'cert.pem'))
        async def keepalive():
            while True:
                for c in clients:
                    if c.pump and not c.failure:
                        await c.send(Message(0));await c.send_udp(c.sdp(1013,bytes(4)))
                await asyncio.sleep(1)
        heartbeat=asyncio.create_task(keepalive())
        for i,(name,password) in enumerate(accounts):
            c=ReferenceClient('127.0.0.1',status['auth_port'],context=context,local_ip=None if args.shared_nat else f'127.0.0.{i+2}')
            clients.append(c);await c.login(name,password);c.start_pump()
        print(json.dumps(dict(stage='authenticated',players=len(clients))),flush=True)
        groups=[clients[i:i+8] for i in range(0,len(clients),8)]
        async def enter(group):
            owner=group[0];p=bytearray(81);p[:5]=b'Bench';p[37]=len(group);p[46]=1;struct.pack_into('<H',p,47,180)
            await owner.send(Message(3010,bytes(p)));entry=await owner.read_until(3100);number=struct.unpack_from('<H',entry.payload)[0]
            for member in group[1:]:await member.send(Message(3070,struct.pack('<HB11s',number,0,b'')));await member.read_until(3100)
            for member in group[1:]:await member.send(Message(4030));await member.read_until(4050)
            await owner.send(Message(4030))
            await asyncio.gather(*(c.read_until(4080) for c in group))
            for c in group:await c.send(Message(4160))
            await asyncio.gather(*(c.read_until(4180) for c in group))
            for c in group:await c.send(Message(8040,struct.pack('<HQI',number,c.grant['uid'],0)))
            ready=await asyncio.gather(*(c.read_until(8070) for c in group));serial=struct.unpack_from('<I',ready[0].payload,8)[0]
            for c in group:c.room_number=number;c.battle_serial=serial;c.pending=[]
        for group in groups:await enter(group)
        child.stdin.write(b'steady\n');await child.stdin.drain()
        async def receive(c):
            nonlocal received
            while True:
                data=await asyncio.get_running_loop().sock_recv(c.udp,65536)
                plain=c.udp_records.open(data);ident=sdp_header(plain)[0]
                if ident==1009:received+=1
        tasks=[asyncio.create_task(receive(c)) for c in clients]
        async def adversary():
            # Unauthenticated malformed UDP and slow TLS, strictly owned endpoints.
            sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setblocking(False);slow=[]
            try:
                for _ in range(8):
                    r,w=await asyncio.open_connection('127.0.0.1',status['auth_port']);slow.append(w)
                while True:
                    for _ in range(40):await asyncio.get_running_loop().sock_sendto(sock,b'bad-record',('127.0.0.1',status['udp_port']))
                    bad=bytearray(108);struct.pack_into('<IQ',bad,0,8120,0x7fffffff)
                    for c in clients[:2]:
                        others=tuple(other.peer_id for other in groups[0] if other is not c)
                        await c.send_udp(c.sdp(1008,encode_game(Message(8071,bytes(bad))),others))
                    await asyncio.sleep(.02)
            finally:
                sock.close()
                for w in slow:w.close()
                for w in slow:
                    with contextlib.suppress(OSError):await w.wait_closed()
        start=time.monotonic();next_frame=start;last_round=start;next_progress=start+30;seq=0;steady_received=0
        while time.monotonic()-start<args.seconds+args.attack_seconds:
            now=time.monotonic()
            if now-start>=args.seconds and flood is None:
                child.stdin.write(b'attack\n');await child.stdin.drain()
                steady_received=received;flood=asyncio.create_task(adversary())
                print(json.dumps(dict(stage='attack_started',seconds=round(now-start),received=received)),flush=True)
            if now-last_round>=150:
                for group in groups:
                    await group[0].send(Message(3110));await asyncio.gather(*(c.read_until(3115) for c in group));await enter(group)
                last_round=time.monotonic();next_frame=last_round
            for group in groups:
                for c in group:
                    raw=bytearray(108);struct.pack_into('<IQ',raw,0,8120,c.grant['uid']);struct.pack_into('<I',raw,15,seq)
                    struct.pack_into('<f',raw,51,float(seq%100));struct.pack_into('<H',raw,97,3002)
                    await c.send_udp(c.sdp(1008,encode_game(Message(8071,bytes(raw))),tuple(other.peer_id for other in group if other is not c)));sent+=1
                    if seq%args.rate==0:
                        attack=bytearray(53);struct.pack_into('<IQ',attack,0,8125,c.grant['uid']);attack[12:14]=b'\1\1';struct.pack_into('<I',attack,19,seq);struct.pack_into('<QI',attack,39,c.grant['uid'],6001187)
                        await c.send_udp(c.sdp(1008,encode_game(Message(8071,bytes(attack))),tuple(other.peer_id for other in group if other is not c)));sent+=1
            seq+=1;next_frame+=1/args.rate
            if now>=next_progress:
                print(json.dumps(dict(stage='steady',seconds=round(now-start),sent=sent,received=received)),flush=True);next_progress+=30
            for task in tasks:
                if task.done():raise RuntimeError('receiver ended: '+str(task.exception()))
            if any(c.failure for c in clients):raise RuntimeError('game reader failed')
            if heartbeat.done():raise RuntimeError('heartbeat ended')
            if flood and flood.done():raise RuntimeError('adversary ended')
            await asyncio.sleep(max(0,next_frame-time.monotonic()))
        if flood:flood.cancel();await asyncio.gather(flood,return_exceptions=True);flood=None
        await asyncio.sleep(1) # bounded delivery drain, not a business retry
        print(json.dumps(dict(stage='steady_complete',seconds=args.seconds,sent=sent,received=received)),flush=True)
    except Exception as exc:
        failures.append(type(exc).__name__+': '+str(exc));raise
    finally:
        tasks.extend(t for t in (heartbeat,flood) if t is not None)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for c in clients:await c.close()
        if child.returncode is None:
            child.stdin.write(b'stop\n');await child.stdin.drain()
            try:await asyncio.wait_for(child.wait(),30)
            except asyncio.TimeoutError:child.kill();await child.wait()
        errors=(await error_task).decode(errors='replace')
        report=dict(players=args.players,duration=args.seconds,attack_seconds=args.attack_seconds,position_hz=args.rate,attack_hz=1,shared_nat=args.shared_nat,varied_inventory=args.varied_inventory,sent=sent,received=received,failures=failures,exit_code=child.returncode,server_stderr=errors[-2000:],network='loopback-only',hash='real scrypt')
        if (root/'worker-report.json').exists():report['server']=json.loads((root/'worker-report.json').read_text())
        report['assessment']=assess(report)
        (root/'report.json').write_text(json.dumps(report,indent=2))
        print(json.dumps(dict(stage='finished',report=str(root/'report.json'),failures=len(failures))),flush=True)

def assess(report):
    """Do not equate a successful harness with capacity/security qualification."""
    server=report.get('server',{});steady=server.get('phases',{}).get('steady',server.get('final',{}))
    percentiles=steady.get('percentiles',{});samples=server.get('samples',[]);n=report['players']
    weighted=sum(min(8,n-i)*(min(8,n-i)-1) for i in range(0,n,8))/n
    expected=round(report['sent']*weighted);ratio=report['received']/expected if expected else 0
    settled=[s for s in samples if s['elapsed']>=300];growth=(settled[-1]['rss']-settled[0]['rss']) if len(settled)>1 else None
    checks=dict(no_harness_failure=not report['failures'] and report['exit_code']==0,
                long_run=report['duration']>=1800 and n==100,
                resource_limits=all(server.get('constraints',{}).get(k,False) for k in ('cpu_limit','memory_limit')),
                loop_p99=percentiles.get('loop_lag_p99_ms',float('inf'))<=20,
                relay_p99=percentiles.get('udp_dispatch_p99_ms',float('inf'))<=100,
                delivery=ratio>=.99,
                memory_stability=growth is not None and growth<=32*1024*1024,
                lease_cleanup=server.get('leases_after_close')==dict(pending=0,active=0))
    return dict(capacity_accepted=all(checks.values()),checks=checks,expected_deliveries=expected,
                received_ratio=ratio,settled_rss_growth_bytes=growth,
                steady_udp_application_mbps=steady.get('udp_tx_bytes',0)*8/max(1,report['duration'])/1e6,
                rss_peak=max((s['rss'] for s in samples),default=None),
                note='99% loopback delivery is an additional quality check; includes re-entry boundaries, excludes UDP/IP/link overhead. This is not a security certification.')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--worker');p.add_argument('--output');p.add_argument('--players',type=int,default=100);p.add_argument('--seconds',type=int,default=1800);p.add_argument('--attack-seconds',type=int,default=60);p.add_argument('--rate',type=int,default=50)
    p.add_argument('--shared-nat',action='store_true');p.add_argument('--varied-inventory',action='store_true')
    args=p.parse_args()
    if args.worker:asyncio.run(worker(args.worker));return
    if not args.output or not 2<=args.players<=100 or args.players%2 or not 1<=args.seconds<=3600 or not 0<=args.attack_seconds<=300 or not 1<=args.rate<=100:p.error('new output, even2..100 players, seconds1..3600, attack0..300, rate1..100 required')
    asyncio.run(run(args))
if __name__=='__main__':main()
