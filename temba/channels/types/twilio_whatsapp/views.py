import phonenumbers
from phonenumbers.phonenumberutil import region_code_for_number
from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.types.twilio.views import SUPPORTED_COUNTRIES
from temba.contacts.models import URN
from temba.utils.fields import SelectWidget
from temba.utils.uuid import uuid4

from ...models import Channel
from ...views import ALL_COUNTRIES, BaseClaimNumberMixin, ClaimViewMixin


class ClaimView(BaseClaimNumberMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, widget=SelectWidget(attrs={"searchable": True}))
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            phone = self.cleaned_data["phone_number"]
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

    def get_twilio_client(self):
        account_sid = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)
        account_token = self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN, None)

        if account_sid and account_token:
            return TwilioClient(account_sid, account_token)
        return None

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
        return []

    def get_supported_countries_tuple(self):
        return ALL_COUNTRIES

    def get_search_url(self):
        return ""

    def get_claim_url(self):
        return reverse("channels.types.twilio_whatsapp.claim")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        account_trial = False
        if self.account:
            account_trial = self.account.type.lower() == "trial"

        context["account_trial"] = account_trial

        context["current_creds_account"] = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)
        return context

    def get_existing_numbers(self, org):
        client = self.get_twilio_client()
        if client:
            twilio_account_numbers = client.api.incoming_phone_numbers.stream(page_size=1000)

        numbers = []
        for number in twilio_account_numbers:
            parsed = phonenumbers.parse(number.phone_number, None)
            numbers.append(
                dict(
                    number=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                    country=region_code_for_number(parsed),
                )
            )

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

        twilio_phone = next(twilio_phones, None)
        if not twilio_phone:
            raise Exception(_("Only existing Twilio WhatsApp number are supported"))

        phone = phonenumbers.format_number(
            phonenumbers.parse(phone_number, None), phonenumbers.PhoneNumberFormat.NATIONAL
        )

        number_sid = twilio_phone.sid

        config = {
            Channel.CONFIG_NUMBER_SID: number_sid,
            Channel.CONFIG_ACCOUNT_SID: self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID),
            Channel.CONFIG_AUTH_TOKEN: self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN),
            Channel.CONFIG_CALLBACK_DOMAIN: callback_domain,
        }

        role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE

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
            schemes=[URN.WHATSAPP_SCHEME],
        )

        return channel

    def remove_api_credentials_from_session(self):
        if self.channel_type.SESSION_ACCOUNT_SID in self.request.session:
            del self.request.session[self.channel_type.SESSION_ACCOUNT_SID]
        if self.channel_type.SESSION_AUTH_TOKEN in self.request.session:
            del self.request.session[self.channel_type.SESSION_AUTH_TOKEN]
