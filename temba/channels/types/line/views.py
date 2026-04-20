from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        address = forms.CharField(
            label=_("Channel ID"), required=True, help_text=_("Channel ID of the LINE channel for the bot.")
        )
        name = forms.CharField(label=_("Name"), max_length=64, required=True, help_text=_("Name of the bot."))
        access_token = forms.CharField(label=_("Access Token"), required=True, help_text=_("Access token of the bot."))
        secret = forms.CharField(label=_("Secret"), required=True, help_text=_("Secret of the bot."))

    form_class = Form

    def form_valid(self, form):
        name = form.cleaned_data.get("name")
        address = form.cleaned_data.get("address")
        secret = form.cleaned_data.get("secret")
        access_token = form.cleaned_data.get("access_token")

        config = {"auth_token": access_token, "secret": secret, "channel_id": address}

        self.object = Channel.create(
            self.request.org, self.request.user, None, self.channel_type, name=name, address=address, config=config
        )

        return super().form_valid(form)
