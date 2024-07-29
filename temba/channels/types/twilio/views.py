from typing import Any

import phonenumbers
from phonenumbers.phonenumberutil import region_code_for_number
from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioException, TwilioRestException
from twilio.rest import Client as TwilioClient

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import OrgPermsMixin
from temba.utils import countries
from temba.utils.fields import InputWidget, SelectWidget
from temba.utils.timezones import timezone_to_country_code
from temba.utils.uuid import uuid4

from ...models import Channel
from ...views import ALL_COUNTRIES, BaseClaimNumberMixin, ChannelTypeMixin, ClaimViewMixin, UpdateChannelForm

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

        def clean(self):
            self.cleaned_data["address"] = self.cleaned_data["phone_number"]
            return super().clean()

    form_class = Form

    def __init__(self, channel_type):
        super().__init__(channel_type)
        self.account = None
        self.client = None

    def pre_process(self, *args, **kwargs):
        try:
            self.client = self.get_twilio_client()
            if not self.client:
                return HttpResponseRedirect(
                    f'{reverse("channels.types.twilio.connect")}?claim_type={self.channel_type.slug}'
                )
            self.account = self.client.api.account.fetch()
        except TwilioRestException:
            return HttpResponseRedirect(
                f'{reverse("channels.types.twilio.connect")}?claim_type={self.channel_type.slug}'
            )

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
        account_trial = False
        if self.account:
            account_trial = self.account.type.lower() == "trial"

        context["account_trial"] = account_trial
        context["current_creds_account"] = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)

        return context

    def get_twilio_client(self):
        account_sid = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)
        account_token = self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN, None)

        if account_sid and account_token:
            return TwilioClient(account_sid, account_token)
        return None

    def get_existing_numbers(self, org):
        client = self.get_twilio_client()
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
        org = self.request.org
        client = self.get_twilio_client()
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

        config = {
            Channel.CONFIG_APPLICATION_SID: new_app.sid,
            Channel.CONFIG_NUMBER_SID: number_sid,
            Channel.CONFIG_ACCOUNT_SID: self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID),
            Channel.CONFIG_AUTH_TOKEN: self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN),
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

    def remove_api_credentials_from_session(self):
        if self.channel_type.SESSION_ACCOUNT_SID in self.request.session:
            del self.request.session[self.channel_type.SESSION_ACCOUNT_SID]
        if self.channel_type.SESSION_AUTH_TOKEN in self.request.session:
            del self.request.session[self.channel_type.SESSION_AUTH_TOKEN]


class SearchView(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        country = forms.ChoiceField(choices=SEARCH_COUNTRY_CHOICES)
        pattern = forms.CharField(max_length=3, min_length=3, required=False)

    form_class = Form
    permission = "channels.channel_claim"

    def form_invalid(self, *args, **kwargs):
        return JsonResponse([], safe=False)

    def get_twilio_client(self):
        account_sid = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)
        account_token = self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN, None)

        if account_sid and account_token:
            return TwilioClient(account_sid, account_token)
        return None  # pragma: no cover

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
        client = self.get_twilio_client()
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


class UpdateForm(UpdateChannelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.add_config_field(
            "account_sid",
            forms.CharField(
                max_length=34,
                label=_("Twilio Account SID"),
                disabled=True,
            ),
            default="",
        )

        self.add_config_field(
            "auth_token",
            forms.CharField(
                max_length=34,
                label=_("Twilio Account Auth Token"),
                required=True,
                widget=InputWidget(),
            ),
            default="",
        )

    def clean(self) -> dict[str, Any]:
        """
        We override the clean method for Twilio we need to make sure we grab the primary auth tokens
        """
        account_sid = self.cleaned_data.get("account_sid", None)
        account_token = self.cleaned_data.get("auth_token", None)

        try:
            client = TwilioClient(account_sid, account_token)

            # get the actual primary auth tokens from twilio and use them
            account = client.api.account.fetch()
            self.cleaned_data["account_sid"] = account.sid
            self.cleaned_data["auth_token"] = account.auth_token
        except Exception:  # pragma: needs cover
            raise ValidationError(
                _("The Twilio account SID and Token seem invalid. Please check them again and retry.")
            )

        return super().clean()

    class Meta(UpdateChannelForm.Meta):
        fields = ("name", "log_policy")


class Connect(ChannelTypeMixin, OrgPermsMixin, SmartFormView):
    class TwilioConnectForm(forms.Form):
        account_sid = forms.CharField(help_text=_("Your Twilio Account SID"), widget=InputWidget(), required=True)
        account_token = forms.CharField(help_text=_("Your Twilio Account Token"), widget=InputWidget(), required=True)

        def clean(self):
            account_sid = self.cleaned_data.get("account_sid")
            account_token = self.cleaned_data.get("account_token")

            try:
                client = TwilioClient(account_sid, account_token)

                # get the actual primary auth tokens from twilio and use them
                account = client.api.account.fetch()
                self.cleaned_data["account_sid"] = account.sid
                self.cleaned_data["account_token"] = account.auth_token
            except Exception:
                raise ValidationError(
                    _("The Twilio account SID and Token seem invalid. Please check them again and retry.")
                )

            return self.cleaned_data

    form_class = TwilioConnectForm
    permission = "channels.channel_claim"
    submit_button_name = "Save"
    field_config = dict(account_sid=dict(label=""), account_token=dict(label=""))
    success_message = "Twilio Account successfully connected."
    template_name = "channels/types/twilio/connect.html"
    menu_path = "/settings/channels/new-channel"
    title = "Connect Twilio"

    def pre_process(self, *args, **kwargs):
        reset_creds = self.request.GET.get("reset_creds", "")
        org = self.request.org

        last_twilio_channel = (
            org.channels.filter(is_active=True, channel_type__in=["T", "TMS", "TWA"]).order_by("-created_on").first()
        )

        if last_twilio_channel and not reset_creds:
            # add the credentials to the session
            self.request.session[self.channel_type.SESSION_ACCOUNT_SID] = last_twilio_channel.config.get(
                Channel.CONFIG_ACCOUNT_SID, ""
            )
            self.request.session[self.channel_type.SESSION_AUTH_TOKEN] = last_twilio_channel.config.get(
                Channel.CONFIG_AUTH_TOKEN, ""
            )

            return HttpResponseRedirect(self.get_success_url())
        return None

    def get_success_url(self):
        claim_type = self.request.GET.get("claim_type", "twilio")

        if claim_type == "twilio_messaging_service":
            return reverse("channels.types.twilio_messaging_service.claim")

        if claim_type == "twilio_whatsapp":
            return reverse("channels.types.twilio_whatsapp.claim")

        if claim_type == "twilio":
            return reverse("channels.types.twilio.claim")

        return reverse("channels.channel_claim")

    def form_valid(self, form):
        account_sid = form.cleaned_data["account_sid"]
        account_token = form.cleaned_data["account_token"]

        # add the credentials to the session
        self.request.session[self.channel_type.SESSION_ACCOUNT_SID] = account_sid
        self.request.session[self.channel_type.SESSION_AUTH_TOKEN] = account_token

        return HttpResponseRedirect(self.get_success_url())
