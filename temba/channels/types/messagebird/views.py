from smartmin.views import SmartFormView
import phonenumbers
from django import forms
from django.utils.translation import gettext_lazy as _
from temba.utils import countries
from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ClaimViewMixin

SUPPORTED_COUNTRIES = {
    "AU",  # Australia
    "AT",  # Austria
    "BE",  # Belgium
    "CA",  # Canada
    "CL",  # Chile
    "CZ",  # Czech Republic
    "DK",  # Denmark  # Beta
    "EE",  # Estonia
    "FI",  # Finland
    "FR",  # France  # Beta
    "DE",  # Germany
    "EE",  # Estonia
    "HK",  # Hong Kong
    "HU",  # Hungary  # Beta
    "IE",  # Ireland,
    "IL",  # Israel  # Beta
    "IT",  # Italy  # Beta
    "LT",  # Lithuania
    "MY",  # Malaysia
    "MX",  # Mexico  # Beta
    "NL",  # Netherlands
    "NO",  # Norway
    "PH",  # Philippines  # Beta
    "PL",  # Poland
    "PR",  # Puerto Rico
    "PT",  # Portugal
    "ES",  # Spain
    "SE",  # Sweden
    "SG",  # Singapore  # Beta
    "CH",  # Switzerland
    "GB",  # United Kingdom
    "US",  # United States
    "VI",  # Virgin Islands
    "VN",  # Vietnam  # Beta
    "ZA",  # South Africa  # Beta
}

COUNTRY_CHOICES = countries.choices(SUPPORTED_COUNTRIES)
CALLING_CODES = countries.calling_codes(SUPPORTED_COUNTRIES)


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        phone_num = forms.CharField(
            required=True,
            label=_("Originating Phone number"),
            help_text=_("The phone number being added"),
        )

        def clean_phone_number(self):
            phone = self.cleaned_data["phone_number"]

            # short code should not be formatted
            if len(phone) <= 6:
                return phone

            phone = phonenumbers.parse(phone, self.cleaned_data["country"])
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

        country = forms.ChoiceField(choices=COUNTRY_CHOICES, widget=SelectWidget(attrs={"searchable": True}))
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
        country = form.cleaned_data.get("country")[0]
        phone_num = self.clean_phone_number()
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
            country,
            self.channel_type,
            address=phone_num,
            name=title,
            config=config,
        )

        return super().form_valid(form)
