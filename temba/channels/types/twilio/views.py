import phonenumbers
from phonenumbers.phonenumberutil import region_code_for_number
from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioException, TwilioRestException

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.models import Org
from temba.orgs.views import OrgPermsMixin
from temba.utils import countries
from temba.utils.fields import SelectWidget
from temba.utils.timezones import timezone_to_country_code
from temba.utils.uuid import uuid4

from ...models import Channel
from ...views import ALL_COUNTRIES, BaseClaimNumberMixin, ClaimViewMixin

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

SEARCH_COUNTRIES = {
    "BE",  # Belgium
    "CA",  # Canada
    "FI",  # Finland
    "NO",  # Norway
    "PL",  # Poland
    "ES",  # Spain
    "SE",  # Sweden
    "GB",  # United Kingdom
    "US",  # United States
}

COUNTRY_CHOICES = countries.choices(SUPPORTED_COUNTRIES)
CALLING_CODES = countries.calling_codes(SUPPORTED_COUNTRIES)
SEARCH_COUNTRY_CHOICES = countries.choices(SEARCH_COUNTRIES)


class ClaimView(BaseClaimNumberMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, widget=SelectWidget(attrs={"searchable": True}))
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            phone = self.cleaned_data["phone_number"]

            # short code should not be formatted
            if len(phone) <= 6:
                return phone

            phone = phonenumbers.parse(phone, self.cleaned_data["country"])
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def __init__(self, channel_type):
        super().__init__(channel_type)
        self.account = None
        self.client = None

    def pre_process(self, *args, **kwargs):
        org = self.request.user.get_org()
        try:
            self.client = org.get_twilio_client()
            if not self.client:
                return HttpResponseRedirect(
                    f'{reverse("orgs.org_twilio_connect")}?claim_type={self.channel_type.slug}'
                )
            self.account = self.client.api.account.fetch()
        except TwilioRestException:
            return HttpResponseRedirect(f'{reverse("orgs.org_twilio_connect")}?claim_type={self.channel_type.slug}')

    def get_search_countries_tuple(self):
        return SEARCH_COUNTRY_CHOICES

    def get_supported_countries_tuple(self):
        return ALL_COUNTRIES

    def get_search_url(self):
        return reverse("channels.types.twilio.search")

    def get_claim_url(self):
        return reverse("channels.types.twilio.claim")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["account_trial"] = self.account.type.lower() == "trial"
        return context

    def get_existing_numbers(self, org):
        client = org.get_twilio_client()
        if client:
            twilio_account_numbers = client.api.incoming_phone_numbers.stream(page_size=1000)
            twilio_short_codes = client.api.short_codes.stream(page_size=1000)

        numbers = []
        for number in twilio_account_numbers:
            parsed = phonenumbers.parse(number.phone_number, None)
            numbers.append(
                dict(
                    number=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                    country=region_code_for_number(parsed),
                )
            )

        org_country = timezone_to_country_code(org.timezone)
        for number in twilio_short_codes:
            numbers.append(dict(number=number.short_code, country=org_country))

        return numbers

    def is_valid_country(self, calling_code: int) -> bool:
        return True

    def is_messaging_country(self, country_code: str) -> bool:
        return country_code in SUPPORTED_COUNTRIES

    def claim_number(self, user, phone_number, country, role):
        org = user.get_org()

        client = org.get_twilio_client()
        twilio_phones = client.api.incoming_phone_numbers.stream(phone_number=phone_number)
        channel_uuid = uuid4()

        # create new TwiML app
        callback_domain = org.get_brand_domain()
        base_url = "https://" + callback_domain
        receive_url = base_url + reverse("courier.t", args=[channel_uuid, "receive"])
        status_url = base_url + reverse("mailroom.ivr_handler", args=[channel_uuid, "status"])
        voice_url = base_url + reverse("mailroom.ivr_handler", args=[channel_uuid, "incoming"])

        new_app = client.api.applications.create(
            friendly_name="%s/%s" % (callback_domain.lower(), channel_uuid),
            sms_method="POST",
            sms_url=receive_url,
            voice_method="POST",
            voice_url=voice_url,
            status_callback_method="POST",
            status_callback=status_url,
            voice_fallback_method="GET",
            voice_fallback_url=f"{settings.STORAGE_URL}/voice_unavailable.xml",
        )

        is_short_code = len(phone_number) <= 6
        tps = 10
        if country in ["US", "CA"]:
            tps = 1

        if is_short_code:
            short_codes = client.api.short_codes.stream(short_code=phone_number)
            short_code = next(short_codes, None)

            if short_code:
                number_sid = short_code.sid
                app_url = "https://" + callback_domain + "%s" % reverse("courier.t", args=[channel_uuid, "receive"])
                client.api.short_codes.get(number_sid).update(sms_url=app_url, sms_method="POST")

                role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
                phone = phone_number
                tps = 100

            else:  # pragma: no cover
                raise Exception(
                    _(
                        "Short code not found on your Twilio Account. "
                        "Please check you own the short code and Try again"
                    )
                )
        else:
            twilio_phone = next(twilio_phones, None)
            if twilio_phone:

                client.api.incoming_phone_numbers.get(twilio_phone.sid).update(
                    voice_application_sid=new_app.sid, sms_application_sid=new_app.sid
                )

            else:  # pragma: needs cover
                twilio_phone = client.api.incoming_phone_numbers.create(
                    phone_number=phone_number, voice_application_sid=new_app.sid, sms_application_sid=new_app.sid
                )

            phone = phonenumbers.format_number(
                phonenumbers.parse(phone_number, None), phonenumbers.PhoneNumberFormat.NATIONAL
            )

            role = ""
            if twilio_phone.capabilities.get("voice", False):
                role += Channel.ROLE_CALL + Channel.ROLE_ANSWER
            if twilio_phone.capabilities.get("sms", False):
                role += Channel.ROLE_SEND + Channel.ROLE_RECEIVE

            number_sid = twilio_phone.sid

        org_config = org.config
        config = {
            Channel.CONFIG_APPLICATION_SID: new_app.sid,
            Channel.CONFIG_NUMBER_SID: number_sid,
            Channel.CONFIG_ACCOUNT_SID: org_config[Org.CONFIG_TWILIO_SID],
            Channel.CONFIG_AUTH_TOKEN: org_config[Org.CONFIG_TWILIO_TOKEN],
            Channel.CONFIG_CALLBACK_DOMAIN: callback_domain,
        }

        channel = Channel.create(
            org,
            user,
            country,
            self.channel_type,
            name=phone,
            address=phone_number,
            role=role,
            config=config,
            uuid=channel_uuid,
            tps=tps,
        )

        return channel


class SearchView(OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        country = forms.ChoiceField(choices=SEARCH_COUNTRY_CHOICES)
        pattern = forms.CharField(max_length=3, min_length=3, required=False)

    form_class = Form
    permission = "channels.channel_claim"

    def form_invalid(self, *args, **kwargs):
        return JsonResponse([], safe=False)

    def search_available(self, client, country: str, **kwargs):
        available_numbers = []

        try:
            available_numbers += client.api.available_phone_numbers(country).local.list(**kwargs)
        except TwilioException:  # pragma: no cover
            pass

        try:
            available_numbers += client.api.available_phone_numbers(country).mobile.list(**kwargs)
        except TwilioException:  # pragma: no cover
            pass

        try:
            available_numbers += client.api.available_phone_numbers(country).toll_free.list(**kwargs)
        except TwilioException:  # pragma: no cover
            pass

        return available_numbers

    def form_valid(self, form, *args, **kwargs):
        org = self.request.user.get_org()
        client = org.get_twilio_client()
        data = form.cleaned_data

        # if the country is not US or CANADA list using contains instead of area code
        if not data["pattern"]:
            available_numbers = self.search_available(client, data["country"])
        elif data["country"] in ["CA", "US"]:
            available_numbers = self.search_available(client, data["country"], area_code=data["pattern"])
        else:
            available_numbers = self.search_available(client, data["country"], contains=data["pattern"])

        numbers = []

        for number in available_numbers:
            numbers.append(
                phonenumbers.format_number(
                    phonenumbers.parse(number.phone_number, None), phonenumbers.PhoneNumberFormat.INTERNATIONAL
                )
            )

        if not numbers:
            if data["country"] in ["CA", "US"]:
                msg = _("Sorry, no numbers found, please enter another area code and try again.")
            else:
                msg = _("Sorry, no numbers found, please enter another pattern and try again.")
            return JsonResponse({"error": str(msg)})

        return JsonResponse(numbers, safe=False)
