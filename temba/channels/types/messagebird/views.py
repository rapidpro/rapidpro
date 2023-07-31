import phonenumbers
import pytz
from smartmin.views import SmartFormView

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


def get_tz_for_countries(countries: dict) -> list:
    """Get a list of timezones for a list of countries"""
    timezones = set()
    for country in countries:
        timezones.update(pytz.country_timezones[country])
    """Add UTC as a timezone"""
    timezones.add("UTC")
    return sorted(timezones)


SUPPORTED_TIMEZONES = get_tz_for_countries(SUPPORTED_COUNTRIES)


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(
            required=True,
            label=_("Originating Phone number"),
            help_text=_("The phone number being added"),
        )
        country = forms.ChoiceField(
            choices=COUNTRY_CHOICES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this channel will be used in"),
        )
        secret = forms.CharField(
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

        def clean_number(self):
            phone = self.cleaned_data["number"]

            # short code should not be formatted
            if len(phone) <= 6:
                return phone

            phone = phonenumbers.parse(phone, self.data["country"])
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def form_valid(self, form):
        country = form.cleaned_data.get("country")
        number = form.cleaned_data.get("number")
        title = f"Messagebird: {number}"
        auth_token = form.cleaned_data.get("auth_token")
        secret = form.cleaned_data.get("secret")
        config = {
            Channel.CONFIG_SECRET: secret,
            Channel.CONFIG_AUTH_TOKEN: auth_token,
        }

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            country,
            self.channel_type,
            address=number,
            name=title,
            config=config,
        )

        return super().form_valid(form)
