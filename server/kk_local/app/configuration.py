"""Trusted local configuration parsing; no database, network or client startup."""
import json
from pathlib import Path


MODES=('offline','lab','auth','sdo','native','public')
PATH_OPTIONS=frozenset(('database','client_root','map_client_root','role_ready_file','runtime',
                        'events','log','report','grant_plan','experimental_multi_account_config','config','auth_certificate','auth_key','security_policy'))


def _unique_object(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('duplicate settings key')
        result[key]=value
    return result


def read_settings(path):
    path=Path(path).resolve(strict=True)
    if path.stat().st_size>1024*1024:raise ValueError('settings file too large')
    data=json.loads(path.read_text(encoding='utf-8-sig'),object_pairs_hook=_unique_object)
    if (not isinstance(data,dict) or set(data)!={'schema','mode','options'} or
            data['schema']!='kk-server-settings-v1' or data['mode'] not in MODES or not isinstance(data['options'],dict)):
        raise ValueError('invalid server settings document')
    return data,path.parent


def settings_arguments(parser,options,base):
    """Convert typed JSON into existing argparse options; never eval/exec."""
    actions={a.dest:a for a in parser._actions if a.option_strings and a.dest!='help'}
    result=[]
    for key,value in options.items():
        if key not in actions:raise ValueError('unsupported settings option: '+key)
        action=actions[key]
        flag=next(s for s in action.option_strings if s.startswith('--'))
        if action.nargs==0:
            if type(value) is not bool:raise ValueError('boolean option required: '+key)
            if value:result.append(flag)
            continue
        if action.type is int:
            if type(value) is not int:raise ValueError('integer option required: '+key)
        elif not isinstance(value,str) or '\0' in value:
            raise ValueError('string option required: '+key)
        if key in PATH_OPTIONS and value!=':memory:':
            value=str((base/value).resolve()) if not Path(value).is_absolute() else value
        result.extend((flag,str(value)))
    return result


def validate_options(mode,args):
    """Pure admission checks before any runner opens logs, SQLite or sockets."""
    outputs={}
    for name in ('database','events','log','report'):
        value=getattr(args,name,None)
        if not value or value==':memory:':continue
        resolved=str(Path(value).resolve()).casefold()
        if resolved in outputs:raise ValueError('output paths overlap: '+outputs[resolved]+' / '+name)
        outputs[resolved]=name
    if mode=='offline':
        rows=account_endpoints(args.experimental_multi_account_config) if args.experimental_multi_account_config else None
        if rows is None and not args.role_ready_file:raise ValueError('single-account endpoint requires role-ready-file')
        from .features import validate_features
        validate_features(args,shared=bool(rows))
        ports=[args.login_port,args.game_port,args.p2p_port]
        if any(type(p) is not int or not 0<=p<=65535 for p in ports):raise ValueError('invalid endpoint port')
        if args.login_port and args.login_port==args.game_port:raise ValueError('distinct login/game TCP ports required')
    elif mode=='public':
        import ipaddress
        from ..public_policy import PublicPolicy
        for field in ('listen_host','advertised_host'):
            address=ipaddress.ip_address(getattr(args,field))
            if address.version!=4 or address.is_multicast or (field=='advertised_host' and address.is_unspecified):raise ValueError('invalid public IPv4 endpoint')
        ports=[args.auth_port,args.sdk_port,args.game_port,args.udp_port]
        if any(not 1<=p<=65535 for p in ports) or len(set(ports[:3]))!=3:raise ValueError('public endpoint ports')
        if not 1<=args.health_port<=65535 or args.health_port in ports[:3]:raise ValueError('health endpoint port')
        if args.database==':memory:':raise ValueError('persistent public database required')
        for field in ('auth_certificate','auth_key','security_policy'):
            if not getattr(args,field):raise ValueError('public security inputs required')
            if str(Path(getattr(args,field)).resolve()).casefold() in outputs:raise ValueError('public output overlaps security input')
        args.public_policy=PublicPolicy.load(args.security_policy)
    elif mode=='native':
        if not args.enable_native_adapter_testing:raise ValueError('native adapter testing must be explicit')
        if args.advertised_host!='127.0.0.1':raise ValueError('public native admission is not yet qualified')
        ports=[args.auth_port,args.sdk_port,args.game_port,args.udp_port]
        if any(type(p) is not int or not 1<=p<=65535 for p in ports) or len(set(ports[:3]))!=3:raise ValueError('invalid native endpoint ports')
        if args.database==':memory:':raise ValueError('persistent native database required')
        if not 1<=args.max_players<=8:raise ValueError('current native room hub limit1..8')
        for name in ('auth_certificate','auth_key'):
            if not getattr(args,name):raise ValueError('TLS certificate and key required')
    elif mode=='lab':
        from .lab import lab_endpoints
        lab_endpoints(args.config)
        from .features import validate_features
        validate_features(args,shared=True)
    else:
        names=('auth_port','login_port','game_port') if mode=='auth' else ('api_port','http_port','login_port','game_port')
        ports=[getattr(args,n) for n in names]
        if len(set(ports))!=len(ports) or any(type(p) is not int or not 1<=p<=65535 for p in ports):
            raise ValueError('distinct valid TCP ports required')


def account_endpoints(path):
    """Explicit trusted local endpoint assignments; no implicit identity guessing."""
    config=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows=config.get('accounts')
    if config.get('schema')!='kk-offline-account-endpoints-v1' or not isinstance(rows,list) or not 1<=len(rows)<=8:
        raise ValueError('invalid offline account endpoint configuration')
    uids=set(); names=set(); tcp=set(); udp=set(); receipts=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'uid','account','nickname','login_port','game_port','p2p_port','role_ready_file'}:
            raise ValueError('unexpected account endpoint fields')
        uid=row['uid']; name=row['account']; nickname=row['nickname']; ready=row['role_ready_file']
        if type(uid) is not int or not 0<uid<=0x7fffffffffffffff or uid in uids:
            raise ValueError('invalid or duplicate account UID')
        if not isinstance(name,str) or not name.isascii() or not name.isalnum() or len(name)>20 or name in names:
            raise ValueError('invalid or duplicate account name')
        if not isinstance(nickname,str) or not 1<=len(nickname.encode('gbk'))<=20 or '\0' in nickname:
            raise ValueError('invalid nickname')
        if not isinstance(ready,str) or not ready or '\0' in ready or not Path(ready).is_absolute():
            raise ValueError('readiness receipt must be an absolute path')
        receipt_key=str(Path(ready).resolve()).casefold()
        if receipt_key in receipts:
            raise ValueError('each client needs an independent readiness receipt')
        for key in ('login_port','game_port','p2p_port'):
            if type(row[key]) is not int or not 1<=row[key]<=65535:
                raise ValueError('invalid endpoint port')
        if row['login_port']==row['game_port'] or row['login_port'] in tcp or row['game_port'] in tcp or row['p2p_port'] in udp:
            raise ValueError('duplicate listening endpoints')
        uids.add(uid); names.add(name); tcp.update((row['login_port'],row['game_port']))
        udp.add(row['p2p_port']); receipts.add(receipt_key)
    return rows


def match_point_policy(args):
    rates={1:getattr(args,'match_win_points',0),0:getattr(args,'match_draw_points',0),
           2:getattr(args,'match_loss_points',0)}
    if any(type(v) is not int or not 0<=v<=1000000 for v in rates.values()):
        raise ValueError('match points must be integers from0 to1000000')
    return rates if any(rates.values()) else None
