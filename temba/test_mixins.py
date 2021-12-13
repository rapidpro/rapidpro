from django.views.generic import TemplateView
from django.test.client import RequestFactory

from temba.contacts.models import ContactGroup
from temba.mixins import NotFoundRedirectMixin
from temba.tests import TembaTest


class NotFoundRedirectMixinTest(TembaTest):
    class DummyView(NotFoundRedirectMixin, TemplateView):
        redirect_checking_model = ContactGroup
        redirect_url = "contacts.contact_list"
        redirect_params = {
            "filter_key": "uuid",
            "filter_value": "group",
            "model_manager": "user_groups",
        }

    class DummyView2(NotFoundRedirectMixin, TemplateView):
        redirect_url = "contacts.contact_list"
        redirect_params = {
            "filter_key": None,
            "filter_value": "uuid",
            "model_manager": "user_groups",
        }

        model = ContactGroup

    def setUp(self):
        super().setUp()

    def test_mixin_redirect(self):
        dummy_view = self.DummyView()
        rf = RequestFactory()
        request = rf.request()
        response = dummy_view.dispatch(request)
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.url, "/contact/")

    def test_mixin_missing_model(self):
        dummy_view = self.DummyView2()
        self.assertEqual(dummy_view.redirect_checking_model, ContactGroup)
        self.assertEqual(dummy_view.redirect_params["filter_key"], dummy_view.redirect_params["filter_value"])
