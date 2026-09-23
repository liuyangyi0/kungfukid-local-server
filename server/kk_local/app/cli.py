"""One application CLI, with explicit authenticated versus lab modes."""
import argparse
import asyncio
import json
from pathlib import Path
import sys
from .configuration import MODES,read_settings,settings_arguments,validate_options


def add_room_options(p):
    p.add_argument('--map-client-root')
    p.add_argument('--experimental-stage21',action='store_true')
    p.add_argument('--experimental-mode10',action='store_true')
    p.add_argument('--experimental-tutorial',action='store_true')
    p.add_argument('--experimental-mode1-spectators',type=int,choices=range(9),default=0)
    p.add_argument('--experimental-team-series-rounds',type=int,choices=(0,1,3,5,7),default=0)
    for name in ('win','draw','loss'):p.add_argument(f'--match-{name}-points',type=int,default=0)


def mode_parser(mode):
    p=argparse.ArgumentParser(prog=f'python -m server.kk_local --mode {mode}')
    if mode=='offline':
        p.description='Explicit loopback-only unauthenticated development adapter'
        p.add_argument('--offline-client-adapter',action='store_true',required=True)
        p.add_argument('--role-ready-file')
        p.add_argument('--experimental-multi-account-config')
        p.add_argument('--database',default='server/.data/local.sqlite3')
        p.add_argument('--grant-plan')
        p.add_argument('--local-training-rewards',action='store_true')
        p.add_argument('--log',default='server/.data/events.jsonl')
        p.add_argument('--report',default='server/.data/report.json')
        p.add_argument('--login-port',type=int,default=8000)
        p.add_argument('--game-port',type=int,default=8001)
        p.add_argument('--p2p-port',type=int,default=8001)
        add_room_options(p)
    elif mode=='lab':
        p.description='Explicit insecure host-only two-VM fixture; not password authentication'
        p.add_argument('--insecure-host-only-lab',action='store_true',required=True)
        for name in ('config','database','events'):p.add_argument('--'+name,required=True)
        p.add_argument('--repair-duplicate-weapon-instances',action='store_true')
        add_room_options(p)
    elif mode=='auth':
        p.description='Local password API with native process attribution'
        for name in ('database','client-root','role-ready-file','events'):p.add_argument('--'+name,required=True)
        p.add_argument('--auth-port',type=int,default=7999)
        p.add_argument('--login-port',type=int,default=8000)
        p.add_argument('--game-port',type=int,default=8001)
    elif mode=='public':
        p.description='Versioned authenticated non-ranked public test service; no client attestation'
        for name in ('database','events','auth-certificate','auth-key','security-policy'):p.add_argument('--'+name,required=True)
        p.add_argument('--listen-host',default='127.0.0.1');p.add_argument('--advertised-host',default='127.0.0.1')
        p.add_argument('--auth-port',type=int,default=17999);p.add_argument('--sdk-port',type=int,default=18000)
        p.add_argument('--game-port',type=int,default=18001);p.add_argument('--udp-port',type=int,default=18001)
        p.add_argument('--health-port',type=int,default=18090)
        p.add_argument('--combat-skill-xml',help='Trusted server SkillProperty XML for correlated guard/hit receipts; optional')
        p.add_argument('--combat-receipt-policy',help='Minimal derived receipt policy; use instead of client XML')
    elif mode=='native':
        p.description='Own native wire development: TLS account API; client/UDP adapter qualification pending; loopback only'
        for name in ('database','events','auth-certificate','auth-key'):p.add_argument('--'+name,required=True)
        p.add_argument('--enable-native-adapter-testing',action='store_true',required=True)
        p.add_argument('--advertised-host',default='127.0.0.1')
        p.add_argument('--auth-port',type=int,default=17999)
        p.add_argument('--sdk-port',type=int,default=18000)
        p.add_argument('--game-port',type=int,default=18001)
        p.add_argument('--udp-port',type=int,default=18001)
        p.add_argument('--max-players',type=int,default=8)
    elif mode=='sdo':
        p.description='Original SDK window compatibility; local password verification, optional cryptography dependency'
        for name in ('client-root','runtime','database'):p.add_argument('--'+name,required=True)
        p.add_argument('--query-probe',action='store_true')
        p.add_argument('--http-port',type=int,default=18082)
        p.add_argument('--api-port',type=int,default=17999)
        p.add_argument('--login-port',type=int,default=18000)
        p.add_argument('--game-port',type=int,default=18001)
    else:raise ValueError('unknown mode')
    return p


def runner(mode):
    #Only the selected mode imports its runtime dependencies. Config check and
    #generic help never import SDO crypto, Windows verifiers, or open a Store.
    if mode=='offline':
        from .offline import run
    elif mode=='lab':
        from .lab import run
    elif mode=='auth':
        from ..auth_service import run
    elif mode=='public':
        from .public import run
    elif mode=='native':
        from .native import run
    else:
        from ..sdo_service import run
    return run


def parse_application(argv):
    selector=argparse.ArgumentParser(add_help=False,allow_abbrev=False)
    selector.add_argument('--mode',choices=MODES)
    selector.add_argument('--settings')
    selector.add_argument('--check-config',action='store_true')
    selected,remaining=selector.parse_known_args(argv)
    data=None;base=None
    if selected.settings:
        data,base=read_settings(selected.settings)
        if selected.mode and selected.mode!=data['mode']:raise ValueError('mode conflicts with settings document')
    mode=selected.mode or (data['mode'] if data else 'offline')
    parser=mode_parser(mode)
    tokens=settings_arguments(parser,data['options'],base) if data else []
    args=parser.parse_args(tokens+remaining)
    validate_options(mode,args)
    if selected.settings:
        source=str(Path(selected.settings).resolve()).casefold()
        for name in ('database','events','log','report'):
            value=getattr(args,name,None)
            if value and value!=':memory:' and str(Path(value).resolve()).casefold()==source:
                raise ValueError('settings file cannot be a runtime output')
    return mode,args,selected.check_config


def run_mode(mode,argv=None,implementation=None):
    """Legacy module entrypoints share the same parser and policy checks."""
    parser=mode_parser(mode);args=parser.parse_args(argv)
    try:validate_options(mode,args)
    except ValueError as exc:parser.error(str(exc))
    try:asyncio.run((implementation or runner(mode))(args))
    except KeyboardInterrupt:pass


def main(argv=None):
    argv=list(sys.argv[1:] if argv is None else argv)
    if not argv or argv in (['-h'],['--help']):
        print('Usage: python -m server.kk_local --mode {auth,sdo,offline,lab,native,public} [mode options]\n'
              '       python -m server.kk_local --settings server.json [--check-config]\n'
              'auth: password API; sdo: original login window; offline/lab: explicit insecure fixtures; native: own-client development; public: invitation-only encrypted service.\n'
              'Use --mode MODE --help for mode options. Legacy offline arguments remain accepted.')
        return
    try:mode,args,check=parse_application(argv)
    except (ValueError,OSError) as exc:raise SystemExit('Configuration error: '+str(exc)) from None
    if check:
        print(json.dumps(dict(status='valid',mode=mode,started=False,scope='configuration_only')))
        return
    try:asyncio.run(runner(mode)(args))
    except KeyboardInterrupt:pass
