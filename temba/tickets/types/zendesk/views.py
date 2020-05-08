import requests

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import TemplateView, View

from temba.utils import json

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


class ManifestView(View):
    """
    Describes our Zendesk channel integration
    """

    def get(self, request, *args, **kwargs):
        domain = settings.BRANDING[settings.DEFAULT_BRAND]["domain"]

        return JsonResponse(
            {
                "name": "Temba",
                "id": domain,
                "author": "Nyaruka",
                "version": "v0.0.1",
                "channelback_files": False,
                "urls": {
                    "admin_ui": f"https://{domain}{reverse('tickets.types.zendesk.admin_ui')}",
                    "pull_url": f"https://{domain}/mr/ticket/zendesk/pull",
                    "channelback_url": f"https://{domain}/mr/ticket/zendesk/channelback",
                    "event_callback_url": f"https://{domain}/mr/ticket/zendesk/event_callback",
                },
            },
            json_dumps_params={"indent": 2},
        )


class AdminUIView(TemplateView):
    """
    Zendesk administrator UI for our channel integration
    """

    template_name = "tickets/types/zendesk/admin_ui.haml"
    return_template = "tickets/types/zendesk/admin_ui_return.haml"

    @xframe_options_exempt
    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, *args, **kwargs):
        token = self.request.POST.get("token")
        if token:
            # TODO test token?

            context = {
                "return_url": self.request.POST["return_url"],
                "name": self.request.POST["name"],
                "metadata": json.dumps({"token": token}),
            }
            return TemplateResponse(request=self.request, template=self.return_template, context=context)

        return super().get(*args, **kwargs)

    def get_context_data(self, **kwargs):
        metadata_raw = self.request.POST["metadata"]
        metadata = json.loads(metadata_raw) if metadata_raw else {}

        context = super().get_context_data(**kwargs)
        context["name"] = self.request.POST["name"]
        context["token"] = metadata.get("token", "")
        context["metadata"] = self.request.POST["metadata"]
        context["subdomain"] = self.request.POST["subdomain"]
        context["return_url"] = self.request.POST["return_url"]
        return context
