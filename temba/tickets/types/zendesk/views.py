import re
from urllib.parse import urlparse

from smartmin.views import SmartFormView, SmartReadView

from django import forms
from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils import timezone, translation
from django.utils.http import urlencode
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.clickjacking import xframe_options_exempt
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from temba.orgs.views import OrgPermsMixin
from temba.utils import json
from temba.utils.text import random_string

from ...models import Ticketer
from ...views import BaseConnectView
from .client import Client, ClientError


class ConnectView(BaseConnectView):
    form_blurb = _(
        "Enter your Zendesk subdomain. You will be redirected to Zendesk where you need to grant access to this "
        "application."
    )

    class Form(BaseConnectView.Form):
        subdomain = forms.CharField(help_text=_("Your subdomain on Zendesk"), required=True)

        def clean_subdomain(self):
            from .type import ZendeskType

            org = self.request.user.get_org()
            data = self.cleaned_data["subdomain"]

            if not re.match(r"^[\w\-]+", data):
                raise forms.ValidationError(_("Not a valid subdomain name."))

            for_domain = org.ticketers.filter(is_active=True, ticketer_type=ZendeskType.slug, config__subdomain=data)
            if for_domain.exists():
                raise forms.ValidationError(_("There is already a ticketing service configured for this subdomain."))

            return data

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

    def get_success_url(self):
        return reverse("tickets.types.zendesk.configure", args=[self.object.uuid])

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
        client = Client(subdomain)
        try:
            access_token = client.get_oauth_token(
                settings.ZENDESK_CLIENT_ID, settings.ZENDESK_CLIENT_SECRET, code, self.get_absolute_url()
            )
        except ClientError:
            messages.error(request, _("Unable to request OAuth token."))
            return super(ConnectView, self).get(request, *args, **kwargs)

        config = {
            ZendeskType.CONFIG_SUBDOMAIN: subdomain,
            ZendeskType.CONFIG_OAUTH_TOKEN: access_token,
            ZendeskType.CONFIG_SECRET: random_string(32),
        }

        self.object = Ticketer.create(
            org=self.org,
            user=self.request.user,
            ticketer_type=ZendeskType.slug,
            name=f"Zendesk ({subdomain})",
            config=config,
        )

        return HttpResponseRedirect(self.get_success_url())


class ConfigureView(OrgPermsMixin, SmartReadView):
    model = Ticketer
    fields = ()
    permission = "tickets.ticketer_configure"
    slug_url_kwarg = "uuid"
    template_name = "tickets/types/zendesk/configure.html"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(org=self.get_user().get_org())

    def get_gear_links(self):
        links = []
        if self.has_org_perm("tickets.ticket_filter"):
            links.append(dict(title=_("Tickets"), href=reverse("tickets.ticket_filter", args=[self.object.uuid])))
        return links

    def get_context_data(self, **kwargs):
        from .type import ZendeskType

        subdomain = self.object.config[ZendeskType.CONFIG_SUBDOMAIN]
        secret = self.object.config[ZendeskType.CONFIG_SECRET]

        context = super().get_context_data(**kwargs)
        context["market_url"] = "https://www.zendesk.com/apps/directory"
        context["channels_url"] = f"https://{subdomain}.zendesk.com/agent/admin/registered_integration_services"
        context["secret"] = secret
        return context


class ManifestView(View):
    """
    Describes our Zendesk channel integration
    """

    def get(self, request, *args, **kwargs):
        brand = self.request.branding
        domain = brand["domain"]

        return JsonResponse(
            {
                "name": brand["name"],
                "id": domain,
                "author": "Nyaruka",
                "version": "v0.0.1",
                "channelback_files": False,
                "push_client_id": settings.ZENDESK_CLIENT_ID,
                "urls": {
                    "admin_ui": f"https://{domain}{reverse('tickets.types.zendesk.admin_ui')}",
                    "channelback_url": f"https://{domain}/mr/tickets/types/zendesk/channelback",
                    "event_callback_url": f"https://{domain}/mr/tickets/types/zendesk/event_callback",
                },
            },
            json_dumps_params={"indent": 2},
        )


class AdminUIView(SmartFormView):
    """
    Zendesk administrator UI for our channel integration
    """

    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.subdomain = kwargs.pop("subdomain")
            super().__init__(**kwargs)

        name = forms.CharField(
            widget=forms.TextInput(attrs={"class": "c-txt__input"}),
            label=_("Name"),
            help_text=_("The display name of this account"),
            required=True,
        )
        secret = forms.CharField(
            widget=forms.TextInput(attrs={"class": "c-txt__input"}),
            label=_("Secret"),
            help_text=_("The secret for the ticketer"),
            required=True,
        )
        return_url = forms.CharField(widget=forms.HiddenInput())
        subdomain = forms.CharField(widget=forms.HiddenInput())
        locale = forms.CharField(widget=forms.HiddenInput())
        instance_push_id = forms.CharField(widget=forms.HiddenInput())
        zendesk_access_token = forms.CharField(widget=forms.HiddenInput())

        def clean_secret(self):
            from .type import ZendeskType

            data = self.cleaned_data["secret"]

            ticketers = Ticketer.objects.filter(
                ticketer_type=ZendeskType.slug, config__subdomain=self.subdomain, config__secret=data, is_active=True
            )
            if not ticketers.exists():
                raise forms.ValidationError(_("Secret is incorrect."))

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
            "name": self.request.POST["name"],
            "secret": metadata.get("secret", ""),
            "return_url": self.request.POST["return_url"],
            "subdomain": self.request.POST["subdomain"],
            "locale": self.request.POST["locale"],
            "instance_push_id": self.request.POST["instance_push_id"],
            "zendesk_access_token": self.request.POST["zendesk_access_token"],
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
        kwargs["subdomain"] = self.request.POST["subdomain"]

        if "data" in kwargs and self.is_initial_request():
            del kwargs["data"]
            del kwargs["files"]
        return kwargs

    def get(self, *args, **kwargs):
        return HttpResponse("Method Not Allowed", status=405)

    def post(self, request, *args, **kwargs):
        translation.activate(self.request.POST.get("locale"))

        # if this is the initial request from Zendesk, then it's not an actual form submission
        if self.is_initial_request():
            return super().get(request, *args, **kwargs)

        return super().post(request, *args, **kwargs)

    def form_valid(self, form):
        from .type import ZendeskType

        subdomain = form.cleaned_data["subdomain"]
        secret = form.cleaned_data["secret"]

        ticketer = Ticketer.objects.get(
            ticketer_type=ZendeskType.slug, config__subdomain=subdomain, config__secret=secret, is_active=True
        )

        # update ticketer config with push credentials we've been given
        ticketer.config[ZendeskType.CONFIG_PUSH_ID] = form.cleaned_data["instance_push_id"]
        ticketer.config[ZendeskType.CONFIG_PUSH_TOKEN] = form.cleaned_data["zendesk_access_token"]
        ticketer.modified_on = timezone.now()
        ticketer.save(update_fields=("config", "modified_on"))

        # go to special return view which redirects back to Zendesk as POST
        context = {
            "return_url": form.cleaned_data["return_url"],
            "name": form.cleaned_data["name"],
            "metadata": json.dumps({"ticketer": str(ticketer.uuid), "secret": secret}),
        }
        return TemplateResponse(request=self.request, template=self.return_template, context=context)
