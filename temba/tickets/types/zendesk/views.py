import requests

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _

from ...models import Ticketer
from ...views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(BaseConnectView.Form):
        subdomain = forms.CharField(help_text=_("Your subdomain on Zendesk"))

    form_class = Form

    def get(self, request, *args, **kwargs):
        # if zendesk is returning to us with an authorization code...
        if request.GET.get("code"):
            return self.handle_code_granted(request, *args, **kwargs)

        # if zendesk is returning to us with an error, display that
        if request.GET.get("error"):
            messages.error(request, request.GET.get("error_description"))

        return super(ConnectView, self).get(request, *args, **kwargs)

    def get_absolute_url(self):
        brand = self.org.get_branding()
        return f"https://{brand['domain']}{reverse('tickets.types.zendesk.connect')}"

    def form_valid(self, form):
        subdomain = form.cleaned_data["subdomain"]
        query = urlencode(
            {
                "response_type": "code",
                "redirect_uri": self.get_absolute_url(),
                "client_id": settings.ZENDESK_CLIENT_ID,
                "scope": "read write",
                "state": subdomain,
            }
        )

        return HttpResponseRedirect(f"https://{subdomain}.zendesk.com/oauth/authorizations/new?{query}")

    def handle_code_granted(self, request, *args, **kwargs):
        from .type import ZendeskType

        code = request.GET["code"]
        subdomain = request.GET["state"]

        response = requests.post(
            f"https://{subdomain}.zendesk.com/oauth/tokens",
            json={
                "grant_type": "authorization_code",
                "code": code,
                "client_id": settings.ZENDESK_CLIENT_ID,
                "client_secret": settings.ZENDESK_CLIENT_SECRET,
                "redirect_uri": self.get_absolute_url(),
                "scope": "read write",
            },
        )

        if response.status_code != 200:
            messages.error(request, _("Unable to request OAuth token."))
            return super(ConnectView, self).get(request, *args, **kwargs)

        resp_json = response.json()

        print(resp_json)

        config = {
            ZendeskType.CONFIG_SUBDOMAIN: subdomain,
            ZendeskType.CONFIG_OAUTH_TOKEN: resp_json["access_token"],
        }

        self.object = Ticketer.create(
            org=self.org,
            user=self.request.user,
            ticketer_type=ZendeskType.slug,
            name=f"Zendesk ({subdomain})",
            config=config,
        )

        # TODO: set up trigger on Zendesk side to callback to us on ticket closures
        # See: https://developer.zendesk.com/rest_api/docs/support/triggers

        return HttpResponseRedirect(self.get_success_url())
