# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import phonenumbers

from uuid import uuid4
from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from phonenumbers.phonenumberutil import region_code_for_number
from smartmin.views import SmartFormView
from twilio import TwilioRestException

from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN
from temba.utils import analytics
from temba.utils.timezones import timezone_to_country_code
from ...models import Channel
from ...views import ClaimViewMixin, TWILIO_SUPPORTED_COUNTRIES, BaseClaimNumberMixin, ALL_COUNTRIES
from ...views import TWILIO_SEARCH_COUNTRIES


class ClaimView(BaseClaimNumberMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES)
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            phone = self.cleaned_data['phone_number']

            # short code should not be formatted
            if len(phone) <= 6:
                return phone

            phone = phonenumbers.parse(phone, self.cleaned_data['country'])
            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def __init__(self, channel_type):
        super(ClaimView, self).__init__(channel_type)
        self.account = None
        self.client = None

    def pre_process(self, *args, **kwargs):
        org = self.request.user.get_org()
        try:
            self.client = org.get_twilio_client()
            if not self.client:
                return HttpResponseRedirect(reverse('orgs.org_twilio_connect'))
            self.account = self.client.accounts.get(org.config[ACCOUNT_SID])
        except TwilioRestException:
            return HttpResponseRedirect(reverse('orgs.org_twilio_connect'))

    def get_search_countries_tuple(self):
        return TWILIO_SEARCH_COUNTRIES

    def get_supported_countries_tuple(self):
        return ALL_COUNTRIES

    def get_search_url(self):
        return reverse('channels.channel_search_numbers')

    def get_claim_url(self):
        return reverse('channels.types.twilio.claim')

    def get_context_data(self, **kwargs):
        context = super(ClaimView, self).get_context_data(**kwargs)
        context['account_trial'] = self.account.type.lower() == 'trial'
        return context

    def get_existing_numbers(self, org):
        client = org.get_twilio_client()
        if client:
            twilio_account_numbers = client.phone_numbers.list()
            twilio_short_codes = client.sms.short_codes.list()

        numbers = []
        for number in twilio_account_numbers:
            parsed = phonenumbers.parse(number.phone_number, None)
            numbers.append(dict(number=phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL),
                                country=region_code_for_number(parsed)))

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
        twilio_phones = client.phone_numbers.list(phone_number=phone_number)
        channel_uuid = uuid4()

        # create new TwiML app
        callback_domain = org.get_brand_domain()
        new_receive_url = "https://" + callback_domain + reverse('courier.t', args=[channel_uuid, 'receive'])
        new_status_url = "https://" + callback_domain + reverse('handlers.twilio_handler', args=['status', channel_uuid])
        new_voice_url = "https://" + callback_domain + reverse('handlers.twilio_handler', args=['voice', channel_uuid])

        new_app = client.applications.create(
            friendly_name="%s/%s" % (callback_domain.lower(), channel_uuid),
            sms_url=new_receive_url,
            sms_method="POST",
            voice_url=new_voice_url,
            voice_fallback_url="https://" + settings.AWS_BUCKET_DOMAIN + "/voice_unavailable.xml",
            voice_fallback_method='GET',
            status_callback=new_status_url,
            status_callback_method='POST'
        )

        is_short_code = len(phone_number) <= 6
        if is_short_code:
            short_codes = client.sms.short_codes.list(short_code=phone_number)

            if short_codes:
                short_code = short_codes[0]
                number_sid = short_code.sid
                app_url = "https://" + callback_domain + "%s" % reverse('handlers.twilio_handler', args=['receive', channel_uuid])
                client.sms.short_codes.update(number_sid, sms_url=app_url, sms_method='POST')

                role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
                phone = phone_number

            else:  # pragma: no cover
                raise Exception(_("Short code not found on your Twilio Account. "
                                  "Please check you own the short code and Try again"))
        else:
            if twilio_phones:
                twilio_phone = twilio_phones[0]
                client.phone_numbers.update(twilio_phone.sid,
                                            voice_application_sid=new_app.sid,
                                            sms_application_sid=new_app.sid)

            else:  # pragma: needs cover
                twilio_phone = client.phone_numbers.purchase(phone_number=phone_number,
                                                             voice_application_sid=new_app.sid,
                                                             sms_application_sid=new_app.sid)

            phone = phonenumbers.format_number(phonenumbers.parse(phone_number, None),
                                               phonenumbers.PhoneNumberFormat.NATIONAL)

            number_sid = twilio_phone.sid

        org_config = org.config
        config = {Channel.CONFIG_APPLICATION_SID: new_app.sid,
                  Channel.CONFIG_NUMBER_SID: number_sid,
                  Channel.CONFIG_ACCOUNT_SID: org_config[ACCOUNT_SID],
                  Channel.CONFIG_AUTH_TOKEN: org_config[ACCOUNT_TOKEN],
                  Channel.CONFIG_CALLBACK_DOMAIN: callback_domain}

        channel = Channel.create(org, user, country, 'T', name=phone, address=phone_number, role=role,
                                 config=config, uuid=channel_uuid)

        analytics.track(user.username, 'temba.channel_claim_twilio', properties=dict(number=phone_number))

        return channel
