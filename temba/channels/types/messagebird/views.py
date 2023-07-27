from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        phone_num = forms.IntegerField(
            required=True,
            label=_("Originating Phone number- US only."),
            help_text=_("The sending phone number or shortcode. Digits only"),
        )
        signing_key = forms.CharField(
            required=True,
            label=_("Messagebird API Signing Key"),
            help_text=_(
                "Signing Key used to verify signatures. See https://developers.messagebird.com/api/#verifying-http-requests"
            ),
        )
        auth_token = forms.CharField(
            required=True,
            label=_("Messagebird API Auth Token"),
            help_text=_("The API auth token"),
        )

    form_class = Form

    def form_valid(self, form):
        phone_num = form.cleaned_data.get("phone_num")
        title = f"Messagebird: {phone_num}"
        auth_token = form.cleaned_data.get("auth_token")
        signing_key = form.cleaned_data.get("signing_key")
        config = {
            Channel.CONFIG_SECRET: signing_key,
            Channel.CONFIG_AUTH_TOKEN: auth_token,
        }

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            "US",
            self.channel_type,
            address=phone_num,
            name=title,
            config=config,
        )

        return super().form_valid(form)
