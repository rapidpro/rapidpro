from django.contrib.auth.models import Group

from temba.api.models import APIToken
from temba.api.tasks import update_tokens_used
from temba.orgs.models import OrgRole
from temba.tests import TembaTest


class APITokenTest(TembaTest):
    def setUp(self):
        super().setUp()

        self.admins_group = Group.objects.get(name="Administrators")
        self.editors_group = Group.objects.get(name="Editors")

        self.org2.add_user(self.admin, OrgRole.EDITOR)  # our admin can act as editor for other org

    def test_create(self):
        token1 = APIToken.create(self.org, self.admin)
        self.assertEqual(self.org, token1.org)
        self.assertEqual(self.admin, token1.user)
        self.assertTrue(token1.key)
        self.assertEqual(str(token1), token1.key)

        # can create another token for same user
        token2 = APIToken.create(self.org, self.admin)
        self.assertNotEqual(token1, token2)
        self.assertNotEqual(token1.key, token2.key)

        # can't create tokens for viewer or agent users
        self.assertRaises(AssertionError, APIToken.create, self.org, self.agent)
        self.assertRaises(AssertionError, APIToken.create, self.org, self.user)

    def test_record_used(self):
        token1 = APIToken.create(self.org, self.admin)
        token2 = APIToken.create(self.org2, self.admin2)

        token1.record_used()

        update_tokens_used()

        token1.refresh_from_db()
        token2.refresh_from_db()

        self.assertIsNotNone(token1.last_used_on)
        self.assertIsNone(token2.last_used_on)
