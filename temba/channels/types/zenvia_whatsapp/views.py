import phonenumbers
import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        number = forms.CharField(
            max_length=18,
            min_length=1,
            label=_("Number"),
            help_text=_(
                "The phone number with country code or short code you are connecting. ex: +250788123124 or 15543"
            ),
        )
        token = forms.CharField(
            label=_("API Token"), help_text=_("The API token for your integration as provided by Zenvia")
        )

        def clean_number(self):
            # if this is a long number, try to normalize it
            number = self.data["number"]
            if len(number) >= 8:
                try:
                    cleaned = phonenumbers.parse(number, self.data["country"])
                    return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
                except Exception:  # pragma: needs cover
                    raise forms.ValidationError(
                        _("Invalid phone number, please include the country code. ex: +250788123123")
                    )
            else:  # pragma: needs cover
                return number

        def clean_token(self):
            token = self.data["token"]
            headers = {
                "X-API-TOKEN": token,
                "Content-Type": "application/json",
            }

            conf_url = "https://api.zenvia.com/v2/subscriptions"

            resp = requests.get(conf_url, headers=headers)

            if resp.status_code != 200:
                raise forms.ValidationError(_("Invalid token. Please check your Zenvia account settings."))

            return token

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        data = form.cleaned_data
        org = user.get_org()

        config = {Channel.CONFIG_API_KEY: data["token"]}

        channel_type_name = ""
        if self.channel_type.code == "ZVW":
            channel_type_name = "WhatsApp"
        if self.channel_type.code == "ZVS":
            channel_type_name = "SMS"

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name=f"Zenvia {channel_type_name}: {data['number']}",
            address=data["number"],
            config=config,
        )

        return super().form_valid(form)
