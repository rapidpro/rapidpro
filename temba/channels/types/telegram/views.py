import requests
from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        auth_token = forms.CharField(
            label=_("Authentication Token"), help_text=_("The Authentication token for your Telegram Bot")
        )

        def clean_auth_token(self):
            org = self.request.org
            value = self.cleaned_data["auth_token"]

            # does a bot already exist on this account with that auth token
            for channel in Channel.objects.filter(org=org, is_active=True, channel_type=self.channel_type.code):
                if channel.config["auth_token"] == value:
                    raise ValidationError(_("A telegram channel for this bot already exists on your account."))

            response = requests.get(f"https://api.telegram.org/bot{value}/getMe")
            response_json = response.json()

            if response.status_code != 200 or not response_json["ok"]:
                raise ValidationError(_("Your authentication token is invalid, please check and try again"))

            return value

    form_class = Form

    def form_valid(self, form):
        org = self.request.org
        auth_token = self.form.cleaned_data["auth_token"]

        response = requests.get(f"https://api.telegram.org/bot{auth_token}/getMe")
        response_json = response.json()

        channel_config = {
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
        }

        self.object = Channel.create(
            org,
            self.request.user,
            None,
            self.channel_type,
            name=response_json.get("result", {}).get("first_name"),
            address=response_json.get("result", {}).get("username"),
            config=channel_config,
        )

        return super().form_valid(form)
