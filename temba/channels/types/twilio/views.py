import phonenumbers
from phonenumbers.phonenumberutil import region_code_for_number
from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioRestException

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org
from temba.utils.timezones import timezone_to_country_code
from temba.utils.uuid import uuid4

from ...models import Channel
from ...views import (
    ALL_COUNTRIES,
    TWILIO_SEARCH_COUNTRIES,
    TWILIO_SUPPORTED_COUNTRIES,
    BaseClaimNumberMixin,
    ClaimViewMixin,
)


class ClaimView(BaseClaimNumberMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES)
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
                return HttpResponseRedirect(reverse("orgs.org_twilio_connect"))
            self.account = self.client.api.account.fetch()
        except TwilioRestException:
            return HttpResponseRedirect(reverse("orgs.org_twilio_connect"))

    def get_search_countries_tuple(self):
        return TWILIO_SEARCH_COUNTRIES

    def get_supported_countries_tuple(self):
        return ALL_COUNTRIES

    def get_search_url(self):
        return reverse("channels.channel_search_numbers")

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

    def is_valid_country(self, country_code):
        return True

    def is_messaging_country(self, country):
        return country in [c[0] for c in TWILIO_SUPPORTED_COUNTRIES]

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
            "T",
            name=phone,
            address=phone_number,
            role=role,
            config=config,
            uuid=channel_uuid,
            tps=tps,
        )

        return channel
