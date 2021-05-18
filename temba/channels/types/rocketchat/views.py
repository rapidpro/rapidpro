import re
from urllib.parse import urlparse

from smartmin.views import SmartFormView

from django import forms
from django.contrib import messages
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import ExternalURLField
from temba.utils.text import random_string, truncate
from temba.utils.uuid import uuid4

from ...models import Channel
from ...views import ClaimViewMixin
from .client import Client, ClientError

UUID_PATTERN = r"[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}"
RE_UUID = re.compile(UUID_PATTERN)
RE_BASE_URL = re.compile(rf"https?://[^ \"]+/{UUID_PATTERN}")

SECRET_LENGTH = 32


class ClaimView(ClaimViewMixin, SmartFormView):
    SESSION_KEY = "_channel_rocketchat_secret"

    _secret = None

    class Form(ClaimViewMixin.Form):
        base_url = ExternalURLField(
            label=_("URL"),
            widget=forms.URLInput(
                attrs={
                    "placeholder": _(
                        "Ex.: https://my.rocket.chat/api/apps/public/51c5cebe-b8e4-48ae-89d3-2b7746019cc4"
                    )
                }
            ),
            help_text=_("URL of the Rocket.Chat Channel app"),
        )
        bot_username = forms.CharField(label=_("Bot Username"), help_text=_("Username of your bot user"))
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
            base_url = RE_BASE_URL.search(self.cleaned_data.get("base_url", ""))
            if base_url:
                base_url = base_url.group()
            else:
                raise forms.ValidationError(_("Invalid URL %(base_url)s") % self.cleaned_data)

            base_url_exists = org.channels.filter(
                is_active=True, channel_type=RocketChatType.code, **{f"config__contains": base_url},
            ).exists()
            if base_url_exists:
                raise forms.ValidationError(_("There is already a channel configured for this URL."))

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
        bot_username = form.cleaned_data["bot_username"]
        admin_auth_token = form.cleaned_data["admin_auth_token"]
        admin_user_id = form.cleaned_data["admin_user_id"]
        secret = form.cleaned_data["secret"]
        config = {
            RocketChatType.CONFIG_BASE_URL: base_url,
            RocketChatType.CONFIG_BOT_USERNAME: bot_username,
            RocketChatType.CONFIG_ADMIN_AUTH_TOKEN: admin_auth_token,
            RocketChatType.CONFIG_ADMIN_USER_ID: admin_user_id,
            RocketChatType.CONFIG_SECRET: secret,
        }

        rc_host = urlparse(base_url).netloc

        self.object = Channel(
            uuid=uuid4(),
            org=self.org,
            channel_type=RocketChatType.code,
            config=config,
            name=truncate(f"{RocketChatType.name}: {rc_host}", Channel._meta.get_field("name").max_length),
            created_by=self.request.user,
            modified_by=self.request.user,
        )

        client = Client(config[RocketChatType.CONFIG_BASE_URL], config[RocketChatType.CONFIG_SECRET])
        webhook_url = "https://" + self.object.callback_domain + reverse("courier.rc", args=[self.object.uuid])

        try:
            client.settings(webhook_url, bot_username)
        except ClientError as err:
            messages.error(self.request, err.msg if err.msg else _("Configuration has failed"))
            return super().get(self.request, *self.args, **self.kwargs)
        else:
            self.request.session.pop(self.SESSION_KEY, None)

        self.object.save()
        return super().form_valid(form)

    def get_context_data(self, **kwargs):
        kwargs["secret"] = self.get_secret()
        return super().get_context_data(**kwargs)

    form_class = Form
    template_name = "channels/types/rocketchat/claim.haml"
