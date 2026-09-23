"""Invitation registration, bounded KDF scheduling and explicit revocation."""
import asyncio
import contextvars
import time
from .auth import AuthManager,AuthError,normalize_account,token_digest
from .storage.public_access import PublicAccessRepository,invite_digest

class PublicAuthManager(AuthManager):
    def __init__(self,store,regions,*,policy,clock=time.time):
        super().__init__(store,regions,clock=clock)
        self.policy=policy;self.access=store.public_access
        self.hash_slots=asyncio.Semaphore(policy.hash_workers);self.hash_admitted=0;self.hash_peak=0
        self.invitation=contextvars.ContextVar('public_invitation',default=None);self.revocation_handlers=[]
        self.records.on_remove=lambda digest:store.after_commit(lambda:self.revoked(digest))
    def revoked(self,digest):
        for callback in tuple(self.revocation_handlers):callback(digest)
    async def _hash(self,encoded,salt):
        if self.hash_admitted>=self.policy.hash_workers+self.policy.hash_waiters:raise AuthError('server_busy')
        self.hash_admitted+=1;self.hash_peak=max(self.hash_peak,self.hash_admitted);owned=False;delegated=False
        try:
            try:await asyncio.wait_for(self.hash_slots.acquire(),self.policy.hash_wait_seconds);owned=True
            except asyncio.TimeoutError:raise AuthError('server_busy') from None
            # Do not release a worker slot merely because a disconnected caller
            # cancelled its future: the scrypt job is still consuming CPU.
            task=asyncio.create_task(super()._hash(encoded,salt))
            def finished(done):
                self.hash_slots.release();self.hash_admitted-=1
                if not done.cancelled():done.exception()
            task.add_done_callback(finished);delegated=True
            return await asyncio.shield(task)
        finally:
            if not delegated:
                if owned:self.hash_slots.release()
                self.hash_admitted-=1
    async def register(self,account,password,nickname=None,invite_code=None):
        if invite_code is None:raise AuthError('invitation_required')
        try:digest=invite_digest(invite_code);self.access.check_invite(digest,int(self.clock()))
        except ValueError:raise AuthError('invitation_invalid') from None
        token=self.invitation.set(digest)
        try:return await super().register(account,password,nickname)
        except ValueError as exc:
            if str(exc)=='invitation_invalid':raise AuthError('invitation_invalid') from None
            raise
        finally:self.invitation.reset(token)
    def _registered_locked(self,uid):
        digest=self.invitation.get()
        if digest is None:raise AuthError('invitation_required')
        self.access.consume(digest,uid,int(self.clock()))
    def _login_allowed_locked(self,uid):return not self.access.disabled(uid)
    def _session(self,token):
        digest,uid=super()._session(token)
        if self.access.disabled(uid):raise AuthError('invalid_session')
        return digest,uid
    def disable(self,uid):
        with self.store.transaction('public disable'):
            self.access.set_disabled(uid,True);self.records.remove_account_sessions(uid)
