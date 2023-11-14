from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        title = forms.CharField(
            max_length=64,
            required=True,
            label=_("FreshChat Environment Title"),
            help_text=_("The name of your environment"),
        )

        webhook_key = forms.CharField(
            required=True,
            label=_("FreshChat Webhook Public Key"),
            help_text=_("Webhook Public Key used to verify signatures"),
        )
        address = forms.CharField(
            required=True, label=_("FreshChat Agent ID"), help_text=_("The UUID of the Agent you want RP to Use.")
        )
        auth_token = forms.CharField(
            required=True, label=_("FreshChat API Auth Token"), help_text=_("The API auth token- leave out the bearer")
        )

    form_class = Form

    def form_valid(self, form):
        title = form.cleaned_data.get("title")
        address = form.cleaned_data.get("address")
        auth_token = form.cleaned_data.get("auth_token")
        webhook_key = form.cleaned_data.get("webhook_key")
        config = {
            Channel.CONFIG_USERNAME: address,
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_SECRET: webhook_key,
        }

        self.object = Channel.create(
            self.request.org, self.request.user, None, self.channel_type, address=address, name=title, config=config
        )

        return super().form_valid(form)
