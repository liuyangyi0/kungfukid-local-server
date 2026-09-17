"""Candidate response contract for SdoBaseClient 2.2.2.0, not a login server.

1001FA40 requires return_code and data, flattens data into the consumer map,
and imports CASTGC from Set-Cookie as tgt. HTTP success alone is not auth.
No listener, account lookup, password bypass, or session issuance lives here.
"""
import json
import re


def response(return_code, data, message=''):
    if type(return_code) is not int or not isinstance(data,dict):
        raise ValueError('invalid_response_contract')
    if any(not isinstance(k,str) or not isinstance(v,str) or '\0' in k+v for k,v in data.items()):
        raise ValueError('flat_nul_free_string_fields_required')
    # Native parser writes return_code first, then data: prevent shadowing it.
    if 'resultCode' in data:
        raise ValueError('result_code_shadowing')
    if not isinstance(message,str) or '\0' in message:
        raise ValueError('invalid_return_message')
    return json.dumps({'return_code':str(return_code),'return_message':message,'data':data},
                      ensure_ascii=True,separators=(',',':')).encode('ascii')


def guid_response(guid, dynamic_key):
    if re.fullmatch(r'[A-Za-z0-9_-]{16,128}',guid) is None:
        raise ValueError('invalid_guid')
    if re.fullmatch(r'[0-9]{20}',dynamic_key) is None:
        raise ValueError('invalid_dynamic_key')
    return response(0,{'guid':guid,'dynamicKey':dynamic_key,'failReason':''})


def authentication_result(*, authenticated_uid, ticket, session_id, failure_code=-10242302):
    """Serialize a prior authentication decision, never make one from a request.

Failure mapping is provisional; default code is the observed account-error UI
branch in sdologin 4AF180, not a recovered server-side error taxonomy.
Success field sufficiency still requires an original SDK callback test.
"""
    if authenticated_uid is None:
        if ticket or session_id or type(failure_code) is not int or failure_code>=0:
            raise ValueError('invalid_failed_authentication_result')
        return response(failure_code,{'failReason':'Local account authentication failed'},'Local account authentication failed')
    if type(authenticated_uid) is not int or authenticated_uid<=0:
        raise ValueError('invalid_authenticated_uid')
    for value in (ticket,session_id):
        if not isinstance(value,str) or re.fullmatch(r'[A-Za-z0-9_-]{16,128}',value) is None:
            raise ValueError('opaque_local_token_required')
    return response(0,{'sndaId':str(authenticated_uid),'ticket':ticket,'sessionId':session_id,
        'nextAction':'0','failReason':'','popWindowFlag':'0','redirectURL':'',
        'accountUpgradeUrl':'','mobile':'','autoLoginSessionKey':'','autoLoginMaxAge':'0'})
