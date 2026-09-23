import unittest
from types import SimpleNamespace
from server.kk_local.native_service import NativeService
from server.kk_local.engine import Phase
from server.kk_local.public_policy import PublicPolicy


class PreparationDeadlineTests(unittest.TestCase):
    def setUp(self):
        self.service=NativeService.__new__(NativeService)
        self.service.public_policy=PublicPolicy()
        self.service.idle_seconds=30
        self.grant=SimpleNamespace(ready=False,admission_deadline=120)

    def test_authenticated_unready_bootstrap_uses_existing_absolute_deadline(self):
        remaining=self.service.game_read_remaining
        self.assertEqual(remaining(self.grant,Phase.BOOTSTRAP,0,None,40),80)
        self.assertEqual(remaining(self.grant,Phase.BOOTSTRAP,119,None,121),-1)

    def test_profile_delivery_starts_normal_idle_once(self):
        self.grant.ready=True
        self.assertEqual(self.service.game_read_remaining(self.grant,Phase.PROFILE,0,41,42),29)
        self.assertEqual(self.service.game_read_remaining(self.grant,Phase.PROFILE,0,41,72),-1)

    def test_no_extension_for_unauthenticated_lobby_or_legacy(self):
        remaining=self.service.game_read_remaining
        self.assertEqual(remaining(None,Phase.BOOTSTRAP,0,None,31),-1)
        self.assertEqual(remaining(self.grant,Phase.LOBBY,0,None,31),-1)
        self.service.public_policy=None
        self.assertEqual(remaining(self.grant,Phase.BOOTSTRAP,0,None,31),-1)
