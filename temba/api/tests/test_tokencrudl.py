from django.urls import reverse

from temba.api.models import APIToken
from temba.orgs.models import OrgRole
from temba.tests import CRUDLTestMixin, TembaTest


class APITokenCRUDLTest(CRUDLTestMixin, TembaTest):
    def test_list(self):
        tokens_url = reverse("api.apitoken_list")

        self.assertRequestDisallowed(tokens_url, [None, self.user, self.agent])
        self.assertListFetch(tokens_url, [self.admin], context_objects=[])

        # add user to other org and create API tokens for both
        self.org2.add_user(self.admin, OrgRole.EDITOR)
        token1 = APIToken.create(self.org, self.admin)
        token2 = APIToken.create(self.org, self.admin)
        APIToken.create(self.org, self.editor)  # other user
        APIToken.create(self.org2, self.admin)  # other org

        response = self.assertListFetch(tokens_url, [self.admin], context_objects=[token1, token2], choose_org=self.org)
        self.assertContentMenu(tokens_url, self.admin, ["New"], choose_org=self.org)

        # can POST to create new token
        response = self.client.post(tokens_url, {})
        self.assertRedirect(response, tokens_url)
        self.assertEqual(3, self.admin.get_api_tokens(self.org).count())
        token3 = self.admin.get_api_tokens(self.org).order_by("created").last()

        # and now option to create new token is gone because we've reached the limit
        response = self.assertListFetch(
            tokens_url, [self.admin], context_objects=[token1, token2, token3], choose_org=self.org
        )
        self.assertContentMenu(tokens_url, self.admin, [], choose_org=self.org)

        # and POSTing is noop
        response = self.client.post(tokens_url, {})
        self.assertRedirect(response, tokens_url)
        self.assertEqual(3, self.admin.get_api_tokens(self.org).count())

    def test_delete(self):
        token1 = APIToken.create(self.org, self.admin)
        token2 = APIToken.create(self.org, self.editor)

        delete_url = reverse("api.apitoken_delete", args=[token1.key])

        self.assertRequestDisallowed(delete_url, [self.editor, self.admin2])

        response = self.assertDeleteFetch(delete_url, [self.admin], as_modal=True)
        self.assertContains(response, f"You are about to delete the API token <b>{token1.key[:6]}…</b>")

        response = self.assertDeleteSubmit(delete_url, self.admin, object_deactivated=token1)
        self.assertRedirect(response, "/apitoken/")

        token1.refresh_from_db()
        token2.refresh_from_db()

        self.assertFalse(token1.is_active)
        self.assertTrue(token2.is_active)
