import re
from urllib.parse import urlparse

from django import forms
from django.contrib import messages
from django.utils.translation import ugettext_lazy as _

from temba.tickets.models import Ticketer
from temba.tickets.views import BaseConnectView
from temba.utils.fields import ExternalURLField
from temba.utils.text import random_string, truncate
from temba.utils.uuid import uuid4

from .client import Client, ClientError

WEBHOOK_URL_TEMPLATE = "https://{domain}/mr/tickets/types/rocketchat/event_callback/{uuid}"

UUID_PATTERN = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
RE_UUID = re.compile(UUID_PATTERN)
RE_BASE_URL = re.compile(rf"https?://[^ \"]+/{UUID_PATTERN}")

SECRET_LENGTH = 32


class ConnectView(BaseConnectView):
    SESSION_KEY = "_ticketer_rocketchat_secret"

    _secret = None

    class Form(BaseConnectView.Form):
        base_url = ExternalURLField(
            label=_("URL"),
            widget=forms.URLInput(
                attrs={
                    "placeholder": _(
                        "Ex.: https://my.rocket.chat/api/apps/public/51c5cebe-b8e4-48ae-89d3-2b7746019cc4"
                    )
                }
            ),
            help_text=_("URL of the Rocket.Chat Tickets app"),
        )
        admin_user_id = forms.CharField(label=_("Admin User ID"), help_text=_("User ID of an administrator user"))
        admin_auth_token = forms.CharField(
            label=_("Admin Auth Token"), help_text=_("Authentication token of an administrator user")
        )
        secret = forms.CharField(
            label=_("Secret"), widget=forms.HiddenInput(), help_text=_("Secret to be passed to Rocket.Chat"),
        )

        def clean(self):
            secret = self.cleaned_data.get("secret")
            if not secret:
                raise forms.ValidationError(_("Invalid secret code."))

            initial = self.initial.get("secret")
            if secret != initial:
                self.data = self.data.copy()
                self.data["secret"] = initial
                raise forms.ValidationError(_("Secret code change detected."))
            return self.cleaned_data

        def clean_base_url(self):
            from .type import RocketChatType

            org = self.request.user.get_org()
            base_url = RE_BASE_URL.search(self.cleaned_data.get("base_url") or "")
            if base_url:
                base_url = base_url.group()
            else:
                raise forms.ValidationError(_("Invalid URL: %(base_url)s") % self.cleaned_data)

            base_url_exists = org.ticketers.filter(
                is_active=True,
                ticketer_type=RocketChatType.slug,
                **{f"config__{RocketChatType.CONFIG_BASE_URL}": base_url},
            ).exists()
            if base_url_exists:
                raise forms.ValidationError(_("There is already a ticketing service configured for this URL."))

            return base_url

    def get_secret(self):
        if self._secret:
            return self._secret

        self._secret = self.request.session.get(self.SESSION_KEY)
        if not self._secret or self.request.method.lower() != "post":
            self.request.session[self.SESSION_KEY] = self._secret = random_string(SECRET_LENGTH)
        return self._secret

    def derive_initial(self):
        initial = super().derive_initial()
        initial["secret"] = self.get_secret()
        return initial

    def form_valid(self, form):
        from .type import RocketChatType

        base_url = form.cleaned_data["base_url"]
        config = {
            RocketChatType.CONFIG_BASE_URL: base_url,
            RocketChatType.CONFIG_SECRET: form.cleaned_data["secret"],
            RocketChatType.CONFIG_ADMIN_AUTH_TOKEN: form.cleaned_data["admin_auth_token"],
            RocketChatType.CONFIG_ADMIN_USER_ID: form.cleaned_data["admin_user_id"],
        }

        rc_host = urlparse(base_url).netloc

        self.object = Ticketer(
            uuid=uuid4(),
            org=self.org,
            ticketer_type=RocketChatType.slug,
            config=config,
            name=truncate(f"{RocketChatType.name}: {rc_host}", Ticketer._meta.get_field("name").max_length),
            created_by=self.request.user,
            modified_by=self.request.user,
        )

        client = Client(config[RocketChatType.CONFIG_BASE_URL], config[RocketChatType.CONFIG_SECRET])
        webhook_url = WEBHOOK_URL_TEMPLATE.format(domain=self.object.org.get_brand_domain(), uuid=self.object.uuid)

        try:
            client.settings(webhook_url)
            self.request.session.pop(self.SESSION_KEY, None)
        except ClientError as err:
            messages.error(self.request, err.msg if err.msg else _("Configuration has failed"))
            return super().get(self.request, *self.args, **self.kwargs)

        self.object.save()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        kwargs["secret"] = self.get_secret()
        return super().get_context_data(**kwargs)

    form_class = Form
    template_name = "tickets/types/rocketchat/connect.haml"
