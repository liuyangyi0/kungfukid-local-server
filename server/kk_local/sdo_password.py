"""Isolated local-auth adapter core; no network listener or implicit game grant.

Consumes the user-mode SDK packet qualified by EXP-20260913-1630. Original
window encoding and process/session handoff still require integrated VM tests.
All challenges are one-use, short-lived, and bound to a caller-supplied verified
client identity. No old private key, plaintext password file or fake auth result.
"""
import hmac
import secrets
import struct
import time
from cryptography.hazmat.primitives.asymmetric import padding
from server.kk_local.auth import AuthError
from .sdo_outer import unwrap_outer

# duilib 100206C0: checksum family over dynamic-key bytes + 14-byte trailer.
_SHIFTS=((5,27),(4,28),(6,7),(7,7),(15,2),(25,27),(14,29),(9,13),
         (21,2),(8,5),(11,14),(11,13),(11,26),(11,25),(11,24),
         (11,23),(11,22),(11,21),(11,20),(8,23),(9,10),(5,15),
         (5,16),(5,18),(7,3),(6,5),(5,10),(3,13),(2,3),(2,2))


def checksum(data, selector):
    if type(selector) is not int or not 0<=selector<len(_SHIFTS):
        raise ValueError('invalid_checksum_selector')
    left,right=_SHIFTS[selector];value=len(data)
    for b in data:
        value=((value<<left) ^ (value>>right) ^ b)&0xffffffff
    return value&0x7fffffff


def decode_inner(packet, dynamic_key, private_key):
    """Qualified v1/kind1/1024-bit user-mode record only; fail closed otherwise."""
    if len(packet)!=164 or private_key.key_size!=1024:
        raise ValueError('invalid_record')
    version,kind,size=struct.unpack_from('<HII',packet)
    selector,expected,suffix_size=struct.unpack_from('<III',packet,138)
    if version!=1 or kind!=1 or size!=128 or suffix_size!=14:
        raise ValueError('invalid_record')
    challenge=dynamic_key.encode('ascii')
    if checksum(challenge+packet[150:],selector)!=expected:
        raise ValueError('invalid_record')
    plain=private_key.decrypt(packet[10:138],padding.PKCS1v15())
    if len(plain)<len(challenge) or not hmac.compare_digest(plain[:len(challenge)],challenge):
        raise ValueError('invalid_record')
    password=plain[len(challenge):]
    # Source's ASCII entry is the verified subset; do not guess legacy Unicode.
    if not 12<=len(password)<=30 or any(b<32 or b>126 for b in password):
        raise ValueError('unsupported_password_encoding')
    return password.decode('ascii')


class PasswordBridge:
    def __init__(self, auth, private_key, *, clock=time.monotonic):
        if private_key.key_size!=1024:
            raise ValueError('qualified_sdk_key_size_required')
        self.auth=auth;self.private_key=private_key;self.clock=clock;self.pending={}

    def issue(self, client_identity):
        if client_identity is None:
            raise AuthError('invalid_credentials')
        now=self.clock()
        self.pending={g:v for g,v in self.pending.items() if v[2]>now}
        if len(self.pending)>=128:
            raise AuthError('rate_limited')
        guid=secrets.token_hex(16)
        key=''.join(f'{secrets.randbelow(65536):05d}' for _ in range(4))
        self.pending[guid]=(client_identity,key,now+30)
        return dict(guid=guid,dynamicKey=key)

    async def authenticate(self, *, client_identity, guid, account_cipher, password_cipher):
        # Consume before decryption/await: failed attempts and concurrent repeats
        # cannot use the same challenge. Wrong identity also consumes the ticket.
        record=self.pending.pop(guid,None) if isinstance(guid,str) else None
        if not record or record[0]!=client_identity or record[2]<=self.clock():
            raise AuthError('invalid_credentials')
        if not isinstance(account_cipher,str) or not isinstance(password_cipher,str) or len(account_cipher)>512 or len(password_cipher)>1024:
            raise AuthError('invalid_credentials')
        try:
            account=unwrap_outer(account_cipher,record[1]).decode('ascii')
            packet=unwrap_outer(password_cipher,record[1])
            password=decode_inner(packet,record[1],self.private_key)
        except (ValueError,TypeError,UnicodeError,struct.error):
            raise AuthError('invalid_credentials') from None
        try:
            return await self.auth.login(account,password)
        except AuthError as exc:
            # Don't expose account, padding, RSA, or packet validation oracles.
            if str(exc)=='rate_limited': raise
            raise AuthError('invalid_credentials') from None
