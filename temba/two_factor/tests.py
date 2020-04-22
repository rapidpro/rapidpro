from django.contrib.auth.models import User
from django.urls import reverse

from temba.orgs.models import UserSettings
from temba.tests import TembaTest


class LoginTest(TembaTest):
    def setUp(self):
        self.user = User.objects.create(username="test", email="test@test.com", password="test")
        self.user_settings = UserSettings.objects.create(user=self.user, two_factor_enabled=True)

    def test_login_with_two_factor_enabled(self):
        response = self.client.post(reverse("two_factor.login"), dict(username="test", password="test"))
        self.assertRedirect(response, reverse("two_factor.token"))

    def test_login_without_two_factor_enabled(self):
        self.user_settings.two_factor_enabled = False
        self.user_settings.save()
        response = self.client.post(reverse("two_factor.login"), dict(username="test", password="test"), follow=True)
        self.assertRedirect(response, reverse("orgs.org_choose"))
