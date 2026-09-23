"""Read-only PE/public-key checks. Never load a DLL, connect, or alter a file."""
import argparse
import hashlib
import json
from pathlib import Path
import struct

FILES=('gfxz-lab.exe','sdo/sdologin/sdologin.exe',
       'sdo/sdologin/SdoBaseClient.dll','sdo/sdologin/duilib.dll')
MAX_FILE=128*1024*1024
EXPECTED_EXPORT16_RVA='0xc1b0'

def sdk_compatibility(summary):
    match=summary.get('export_ordinal_16_rva')==EXPECTED_EXPORT16_RVA
    return {
        'expected_export16_rva':EXPECTED_EXPORT16_RVA,
        'expected_export16_rva_match':match,
        'adapter_compatibility':'candidate_only' if match else 'unsupported_sdk_layout',
        'compatibility_note':(
            'Matching one export does not prove the SDK bundle, ABI or object layouts compatible.' if match else
            'This SDK does not match the original-window adapter. Do not change the expected RVA or bypass the guard; version-specific ABI and object layouts need verification.'),
        'compatibility_document':'docs/CLIENT_COMPATIBILITY.md#sdk-build-compatibility',
    }

def pe_summary(data):
    def read(fmt,offset):
        n=struct.calcsize(fmt)
        if offset<0 or offset+n>len(data):raise ValueError('truncated PE')
        return struct.unpack_from(fmt,data,offset)[0]
    if data[:2]!=b'MZ':raise ValueError('missing MZ')
    pe=read('<I',60)
    if data[pe:pe+4]!=b'PE\0\0':raise ValueError('missing PE signature')
    machine=read('<H',pe+4);count=read('<H',pe+6);optional_size=read('<H',pe+20)
    optional=pe+24;magic=read('<H',optional)
    if magic not in (0x10b,0x20b):raise ValueError('unknown PE optional header')
    if not 1<=count<=96 or optional_size<(96 if magic==0x10b else 112):raise ValueError('invalid PE header bounds')
    base=read('<I' if magic==0x10b else '<Q',optional+(28 if magic==0x10b else 24))
    table=optional+optional_size;sections=[]
    for i in range(count):
        pos=table+40*i
        rva=read('<I',pos+12);size=read('<I',pos+16);raw=read('<I',pos+20)
        if raw+size>len(data):raise ValueError('section outside file')
        sections.append((rva,size,raw))
    def offset(rva,n):
        for start,size,raw in sections:
            if start<=rva and rva+n<=start+size:return raw+rva-start
        raise ValueError('RVA is not file-backed')
    ordinal16=None
    dirs=optional+(96 if magic==0x10b else 112)
    if optional_size>=dirs-optional+8 and read('<I',dirs):
        export=offset(read('<I',dirs),40)
        first=read('<I',export+16);nfunc=read('<I',export+20)
        if first<=16<first+nfunc:
            ordinal16=read('<I',offset(read('<I',export+28)+4*(16-first),4))
    return {'machine':hex(machine),'i686':machine==0x14c and magic==0x10b,
            'image_base':hex(base),'export_ordinal_16_rva':hex(ordinal16) if ordinal16 is not None else None}

def public_key_summary(data):
    if len(data)!=258:raise ValueError('public key must be258 bytes, not PEM')
    bits=struct.unpack_from('<H',data)[0]
    modulus=int.from_bytes(data[2:130],'big');exponent=int.from_bytes(data[130:258],'big')
    if bits!=1024 or modulus.bit_length()!=1024 or modulus%2!=1 or exponent!=3:
        raise ValueError('incompatible legacy public key format')
    return {'bytes':258,'bits':bits,'exponent':exponent,'format_valid':True,
            'private_key_read':False,'paired_with_running_service':'not_checked'}

def bounded_read(path):
    if not path.is_file() or path.stat().st_size>MAX_FILE:raise ValueError('missing or oversized file')
    return path.read_bytes()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--client-root');p.add_argument('--public-key');a=p.parse_args()
    if not a.client_root and not a.public_key:p.error('provide --client-root and/or --public-key')
    result={'schema':'local-client-readonly-check-v1','files':[],
            'version_compatibility_proven':False,'client_executed':False}
    failures=0
    if a.client_root:
        root=Path(a.client_root).resolve(strict=True)
        for name in FILES:
            row={'file':name}
            try:
                path=(root/name).resolve(strict=True)
                if not path.is_relative_to(root):raise ValueError('file link escapes client root')
                data=bounded_read(path);row.update(pe_summary(data))
                row.update(size=len(data),sha256=hashlib.sha256(data).hexdigest())
                if not row['i686']:failures+=1
                if name.endswith('SdoBaseClient.dll'):
                    row.update(sdk_compatibility(row))
                    failures+=not row['expected_export16_rva_match']
            except (ValueError,OSError) as e:
                row['error']=type(e).__name__;failures+=1
            result['files'].append(row)
    if a.public_key:
        try:result['public_key']=public_key_summary(bounded_read(Path(a.public_key)))
        except (ValueError,OSError) as e:result['public_key']={'error':type(e).__name__};failures+=1
    result['structural_failures']=failures
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return int(bool(failures))

if __name__=='__main__':raise SystemExit(main())
