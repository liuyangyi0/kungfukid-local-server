"""Basic public/online-private chat; native7DD1C0/7DD3A0 ->823CB0/823B10."""
import struct
import unicodedata
from .wire import Message,ProtocolError


def system_notice(text):
    """Local 215-byte envelope for native A27E00's 20150 text prefix.

    Native reads length at12 (<201), string at13, and writes NUL at13+n.
    Unconsumed prefix is zero locally; this is not a recovered server error code.
    """
    if not isinstance(text,str) or not text or any(unicodedata.category(c).startswith('C') for c in text):
        raise ProtocolError('invalid system notice text')
    try:raw=text.encode('gbk')
    except UnicodeEncodeError as e:raise ProtocolError('invalid system notice encoding') from e
    if len(raw)>199:raise ProtocolError('system notice too long')
    out=bytearray(215);out[12]=len(raw)+1;out[13:13+len(raw)]=raw
    return Message(20150,bytes(out))


def _text(payload,length_offset,text_offset):
    n=payload[length_offset]
    # Native receiver writes an extra NUL at55+n. Keep it within256 bytes.
    if not 2<=n<=200 or payload[text_offset+n-1]!=0:
        raise ProtocolError('public chat text length/terminator')
    raw=payload[text_offset:text_offset+n-1]
    if b'\0' in raw or any(payload[text_offset+n:]):raise ProtocolError('public chat padding')
    try:text=raw.decode('gbk')
    except UnicodeDecodeError as e:raise ProtocolError('public chat encoding') from e
    if any(unicodedata.category(c).startswith('C') for c in text):
        raise ProtocolError('public chat control characters')
    return raw


def public_text(payload):
    if not isinstance(payload,bytes) or len(payload)!=215:raise ProtocolError('public chat length')
    return _text(payload,12,13)


def private_text(payload):
    if not isinstance(payload,bytes) or len(payload)!=256:raise ProtocolError('private chat length')
    name,sep,padding=payload[29:50].partition(b'\0')
    if not name or not sep or any(padding):raise ProtocolError('private recipient field')
    try:recipient=name.decode('gbk')
    except UnicodeDecodeError as e:raise ProtocolError('private recipient encoding') from e
    return recipient,_text(payload,50,55)


def chat_reply(uid,nickname,raw,*,recipient=None):
    name=nickname.encode('gbk')
    if not 0<uid<=0x7fffffffffffffff or not 1<=len(name)<=20 or b'\0' in name or not 1<=len(raw)<=199:
        raise ProtocolError('public chat response range')
    out=bytearray(256)
    struct.pack_into('<Q',out,0,uid);out[8:8+len(name)]=name
    if recipient is not None:
        target=recipient.encode('gbk')
        if not 1<=len(target)<=20 or b'\0' in target:raise ProtocolError('private recipient range')
        out[29:29+len(target)]=target
    out[50]=len(raw)+1;out[55:55+len(raw)]=raw
    return Message(5001 if recipient is not None else 5003,bytes(out))
