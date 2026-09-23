"""Finite labels, histogram summaries and rotating secret-free operational log."""
import asyncio
from collections import Counter
import json
import logging
from logging.handlers import RotatingFileHandler
import math
import bisect
import time

class Metrics:
    EVENTS=frozenset(('auth_request','native_udp_rejection_summary','native_sdk_authenticated','native_game_login','native_lobby_ready','native_sdk_closed','native_tcp_closed'))
    CLOSE_REASONS=frozenset(('AuthError','ProtocolError','RecordError','IncompleteReadError','TimeoutError','ValueError','OverflowError','OSError','ConnectionResetError','BrokenPipeError','DatabaseError','OperationalError'))
    HANDSHAKE_IDS=frozenset((1010,1130,1151,1190,2010,2015,2250,2254))
    BOUNDS=(.00005,.0001,.00025,.0005,.001,.002,.004,.008,.01,.015,.02,.025,.032,.05,.1,.2,.5,1.,2.,5.,10.,60.)
    RESULT_REJECTIONS={
        '4110 requires eight 87-byte slots':'length',
        '4110 nonempty unoccupied slot':'vacant_slot',
        '4110 roster identity mismatch':'roster',
        '4110 stale room or battle':'stale_battle',
        '4110 HP bounds':'hp_bounds',
        '4110 empty roster':'empty_roster',
        'settlement roster mismatch':'settlement_roster',
        'settlement peer HP disagreement':'peer_hp_disagreement',
        '4110 changed duplicate report':'changed_duplicate',
    }
    DATAGRAM_REJECTIONS={
        'SDP room binding required':'room_binding',
        'SDP target outside room or lease':'target_lease',
        'SDP target reachability unconfirmed':'target_unconfirmed',
        'SDP lease rejected':'sender_lease',
        'SDP relay shape':'relay_shape',
        'SDP target missing':'target_missing',
        'public UDP direction':'direction',
        'public battle context':'battle_context',
        'public battle family unavailable':'battle_family',
        'public actor spoof':'actor',
        'public battle header':'battle_header',
        'public battle reference outside room':'battle_reference',
        'public stale battle':'stale_battle',
        'public battle work budget':'battle_budget',
    }
    def __init__(self):self.counts=Counter();self.histograms={};self.udp_rx_bytes=0;self.udp_tx_bytes=0;self.udp_queue_peak=0
    def protocol_rejection(self,ident,exc):
        if ident==4110:
            code=self.RESULT_REJECTIONS.get(str(exc),'other')
            self.counts['native_4110_rejected_'+code]+=1
        elif ident==8071:
            self.counts['native_8071_protocol_rejected']+=1
    def datagram_rejection(self,exc):
        code=self.DATAGRAM_REJECTIONS.get(str(exc),'other')
        self.counts['udp_rejection_detail_'+code]+=1
    def event(self,row):
        event=row.get('event');key=event if event in self.EVENTS else 'other'
        if event=='native_handshake_frame':
            direction=row.get('direction');ident=row.get('id')
            if direction in ('rx','tx') and type(ident) is int and ident in self.HANDSHAKE_IDS:
                self.counts[f'native_handshake_{direction}_{ident}']+=1
            else:self.counts['native_handshake_other']+=1
            return
        self.counts[key]+=1
        if event in ('native_sdk_closed','native_tcp_closed'):
            reason=row.get('reason');reason=reason if reason in self.CLOSE_REASONS else 'other'
            self.counts[f'{event}_{reason}']+=1
        if event=='native_udp_rejection_summary':
            from .native_udp_policy import RejectionSummary
            for reason,count in row.get('counts',{}).items():
                if reason in RejectionSummary.REASONS:
                    self.counts['udp_rejected']+=count;self.counts['udp_rejected_'+reason]+=count
    def observe(self,name,seconds):
        if name not in ('loop_lag','udp_dispatch','game_dispatch','database'):return
        buckets=self.histograms.setdefault(name,[0]*len(self.BOUNDS))
        index=min(len(self.BOUNDS)-1,bisect.bisect_left(self.BOUNDS,max(0,seconds)))
        buckets[index]+=1
    def snapshot(self):
        percentiles={}
        for name,buckets in self.histograms.items():
            total=sum(buckets);target=math.ceil(total*.99);count=0
            for i,n in enumerate(buckets):
                count+=n
                if count>=target:percentiles[name+'_p99_ms']=self.BOUNDS[i]*1000;break
        return dict(counts=dict(self.counts),percentiles=percentiles,udp_rx_bytes=self.udp_rx_bytes,udp_tx_bytes=self.udp_tx_bytes,udp_queue_peak=self.udp_queue_peak)

class RotatingSummary:
    def __init__(self,path,metrics):
        self.metrics=metrics;self.handler=RotatingFileHandler(path,maxBytes=10*1024*1024,backupCount=5,encoding='utf-8');self.handler.setFormatter(logging.Formatter('%(message)s'))
    def emit(self):
        row=dict(timestamp=time.time(),**self.metrics.snapshot())
        self.handler.emit(logging.LogRecord('public',logging.INFO,'',0,json.dumps(row),(),None))
    def close(self):self.handler.close()

async def health_server(metrics,game,host='127.0.0.1',port=0):
    # No remote admin actions, tokens or arbitrary labels.
    active=set();tasks=set()
    async def handle(reader,writer):
        if len(active)>=8:writer.close();return
        active.add(writer);tasks.add(asyncio.current_task())
        try:
            head=await asyncio.wait_for(reader.readuntil(b'\r\n\r\n'),2)
            if len(head)>2048:raise ValueError()
            line=head.split(b'\r\n',1)[0]
            if line not in (b'GET /healthz HTTP/1.1',b'GET /readyz HTTP/1.1',b'GET /metrics HTTP/1.1'):raise ValueError()
            ready=game.listener is not None and game.sdk_listener is not None and game.udp is not None
            body=json.dumps(metrics.snapshot() if b'/metrics' in line else dict(service_ready=ready)).encode()
            writer.write(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body)
            await asyncio.wait_for(writer.drain(),2)
        except (ValueError,OSError,asyncio.TimeoutError,asyncio.IncompleteReadError,asyncio.LimitOverrunError):pass
        finally:
            active.discard(writer);tasks.discard(asyncio.current_task());writer.close()
    server=await asyncio.start_server(handle,host,port,limit=2048)
    class HealthListener:
        sockets=server.sockets
        def close(self):
            server.close()
            for writer in tuple(active):writer.close()
            for task in tuple(tasks):task.cancel()
        async def wait_closed(self):
            await server.wait_closed()
            if tasks:await asyncio.gather(*tuple(tasks),return_exceptions=True)
    return HealthListener()
