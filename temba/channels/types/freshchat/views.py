

from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        title = forms.CharField(required=True, label=_("FreshChat Environment Title"), help_text=_("The name of your environment"))

        password = forms.CharField(
            required=True,
            label=_("FreshChat Webhook Public Key"),
            help_text=_("Webhook Public Key used to verify signatures"),
        )
        username = forms.CharField(
            required=True, label=_("FreshChat Agent ID"), help_text=_("The ID of the Agent you want RP to Use.")
        )
        auth_token = forms.CharField(
            required=True, label=_("FreshChat API Auth Token"), help_text=_("The API auth token- leave out the bearer")
        )

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        title = form.cleaned_data.get("title")
        username = form.cleaned_data.get("username")
        auth_token = form.cleaned_data.get("auth_token")
        password = form.cleaned_data.get("password")
        config = {
            Channel.CONFIG_USERNAME: username,
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_PASSWORD: password,
        }

        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=title, config=config
        )

        return super().form_valid(form)
