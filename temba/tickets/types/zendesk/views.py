from urllib.parse import urlparse

import requests
from smartmin.views import SmartFormView

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from temba.api.models import APIToken
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
                "push_client_id": "temba",
                "urls": {
                    "admin_ui": f"https://{domain}{reverse('tickets.types.zendesk.admin_ui')}",
                    "channelback_url": f"https://{domain}/mr/ticket/zendesk/channelback",
                    "event_callback_url": f"https://{domain}/mr/ticket/zendesk/event_callback",
                },
            },
            json_dumps_params={"indent": 2},
        )


class AdminUIView(SmartFormView):
    """
    Zendesk administrator UI for our channel integration
    """

    class Form(forms.Form):
        name = forms.CharField(
            widget=forms.TextInput(attrs={"class": "c-txt__input"}),
            label=_("Name"),
            help_text=_("The display name of this account"),
            required=True,
        )
        token = forms.CharField(
            widget=forms.TextInput(attrs={"class": "c-txt__input"}),
            label=_("API Token"),
            help_text=_("Your API token"),
            required=True,
        )
        return_url = forms.CharField(widget=forms.HiddenInput())
        subdomain = forms.CharField(widget=forms.HiddenInput())
        instance_push_id = forms.CharField(widget=forms.HiddenInput())
        zendesk_access_token = forms.CharField(widget=forms.HiddenInput())

        def clean_token(self):
            data = self.cleaned_data["token"]
            if not APIToken.objects.filter(is_active=True, user__is_active=True, key=data).exists():
                raise forms.ValidationError(_("Invalid API token"))
            return data

    form_class = Form
    template_name = "tickets/types/zendesk/admin_ui.haml"
    return_template = "tickets/types/zendesk/admin_ui_return.haml"

    @xframe_options_exempt
    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        """
        Zendesk opens this view in an iframe and the above decorators ensure we allow that
        """
        return super().dispatch(*args, **kwargs)

    def derive_submit_button_name(self):
        return _("Save") if self.request.POST.get("metadata") else _("Add")

    def derive_initial(self):
        metadata = self.request.POST.get("metadata")
        metadata = json.loads(metadata) if metadata else {}
        return {
            "name": self.request.POST.get("name"),
            "token": metadata.get("token", ""),
            "return_url": self.request.POST.get("return_url"),
            "subdomain": self.request.POST.get("subdomain"),
            "instance_push_id": self.request.POST.get("instance_push_id"),
            "zendesk_access_token": self.request.POST.get("zendesk_access_token"),
        }

    def is_initial_request(self):
        """
        When Zendesk initially requests this view, it makes a POST, which we don't want to confuse with a POST
        of the form, so we check the referer.
        """
        referer = urlparse(self.request.META.get("HTTP_REFERER", "")).netloc
        return referer.endswith("zendesk.com")

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if "data" in kwargs and self.is_initial_request():
            del kwargs["data"]
            del kwargs["files"]
        return kwargs

    def post(self, request, *args, **kwargs):
        translation.activate(self.request.POST.get("locale"))

        # if this is the initial request from Zendesk, then it's not an actual form submission
        if self.is_initial_request():
            return super().get(request, *args, **kwargs)

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        from .type import ZendeskType

        subdomain = form.cleaned_data["subdomain"]
        token = APIToken.objects.get(is_active=True, user__is_active=True, key=form.cleaned_data["token"])
        config = {
            "subdomain": subdomain,
            "instance_push_id": form.cleaned_data["instance_push_id"],
            "push_token": form.cleaned_data["zendesk_access_token"],
        }

        # look for existing Zendesk ticketer for this domain
        ticketer = token.org.ticketers.filter(
            ticketer_type=ZendeskType.slug, config__subdomain=subdomain, is_active=True
        ).first()

        if ticketer:
            ticketer.config = config
            ticketer.modified_on = timezone.now()
            ticketer.modified_by = token.user
            ticketer.save(update_fields=("config", "modified_on", "modified_by"))
        else:
            Ticketer.create(
                org=token.org,
                user=token.user,
                ticketer_type=ZendeskType.slug,
                name=f"Zendesk ({subdomain})",
                config=config,
            )

        # go to special return view which redirects back to Zendesk as POST
        context = {
            "return_url": form.cleaned_data["return_url"],
            "name": form.cleaned_data["name"],
            "metadata": json.dumps({"token": token.key}),
        }
        return TemplateResponse(request=self.request, template=self.return_template, context=context)
