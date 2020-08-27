# import telegram
from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        auth_token = forms.CharField(
            label=_("Authentication Token"), help_text=_("The discord bot token")
        )
        proxy_url = forms.CharField(
            label=_("Proxy URL"), help_text=_("The URL on which the discord proxy is running")
        )

        def clean_auth_token(self):
            org = self.request.user.get_org()
            value = self.cleaned_data["auth_token"]

            does a bot already exist on this account with that auth token
            for channel in Channel.objects.filter(org=org, is_active=True, channel_type=self.channel_type.code):
                if channel.config["auth_token"] == value:
                    raise ValidationError(_("A channel for this bot already exists on your account."))

            return value

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        auth_token = self.form.cleaned_data["auth_token"]
        proxy_url = self.form.cleaned_data["proxy_url"]

        channel_config = {
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
            Channel.CONFIG_SEND_URL: proxy_url + "/discord/rp/send"
        }

        self.object = Channel.create(
            org,
            self.request.user,
            None,
            self.channel_type,
            config=channel_config,
        )

        return super().form_valid(form)
