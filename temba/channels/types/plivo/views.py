# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import phonenumbers
import pycountry
import requests

from django.conf import settings
from django.core.exceptions import ValidationError
from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView

from temba.channels.models import Channel
from temba.channels.views import BaseClaimNumberMixin, ClaimViewMixin, PLIVO_SUPPORTED_COUNTRIES
from temba.channels.views import PLIVO_SUPPORTED_COUNTRY_CODES
from temba.utils import analytics
from temba.utils.http import http_headers
from temba.utils.models import generate_uuid


class ClaimView(BaseClaimNumberMixin, SmartFormView):

    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=PLIVO_SUPPORTED_COUNTRIES)
        phone_number = forms.CharField(help_text=_("The phone number being added"))

        def clean_phone_number(self):
            if not self.cleaned_data.get('country', None):  # pragma: needs cover
                raise ValidationError(_("That number is not currently supported."))

            phone = self.cleaned_data['phone_number']
            phone = phonenumbers.parse(phone, self.cleaned_data['country'])

            return phonenumbers.format_number(phone, phonenumbers.PhoneNumberFormat.E164)

    form_class = Form

    def pre_process(self, *args, **kwargs):
        auth_id = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_ID, None)
        auth_token = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_TOKEN, None)

        headers = http_headers(extra={'Content-Type': "application/json"})
        response = requests.get("https://api.plivo.com/v1/Account/%s/" % auth_id, headers=headers, auth=(auth_id, auth_token))

        if response.status_code == 200:
            return None
        else:
            return HttpResponseRedirect(reverse('orgs.org_plivo_connect'))

    def is_valid_country(self, country_code):
        return country_code in PLIVO_SUPPORTED_COUNTRY_CODES

    def is_messaging_country(self, country):
        return country in [c[0] for c in PLIVO_SUPPORTED_COUNTRIES]

    def get_search_url(self):
        return reverse('channels.channel_search_plivo')

    def get_claim_url(self):
        return reverse('channels.types.plivo.claim')

    def get_supported_countries_tuple(self):
        return PLIVO_SUPPORTED_COUNTRIES

    def get_search_countries_tuple(self):
        return PLIVO_SUPPORTED_COUNTRIES

    def get_existing_numbers(self, org):
        auth_id = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_ID, None)
        auth_token = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_TOKEN, None)

        headers = http_headers(extra={'Content-Type': "application/json"})
        response = requests.get("https://api.plivo.com/v1/Account/%s/Number/" % auth_id, headers=headers, auth=(auth_id, auth_token))

        account_numbers = []
        if response.status_code == 200:
            data = response.json()
            for number_dict in data['objects']:
                region = number_dict['region']
                country_name = region.split(',')[-1].strip().title()
                country = pycountry.countries.get(name=country_name).alpha_2
                if len(number_dict['number']) <= 6:
                    phone_number = number_dict['number']
                else:
                    parsed = phonenumbers.parse('+' + number_dict['number'], None)
                    phone_number = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
                account_numbers.append(dict(number=phone_number, country=country))

        return account_numbers

    def claim_number(self, user, phone_number, country, role):

        auth_id = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_ID, None)
        auth_token = self.request.session.get(Channel.CONFIG_PLIVO_AUTH_TOKEN, None)

        org = user.get_org()

        plivo_uuid = generate_uuid()
        callback_domain = org.get_brand_domain()
        app_name = "%s/%s" % (callback_domain.lower(), plivo_uuid)

        message_url = "https://" + callback_domain + "%s" % reverse('handlers.plivo_handler', args=['receive', plivo_uuid])
        answer_url = "https://" + settings.AWS_BUCKET_DOMAIN + "/plivo_voice_unavailable.xml"

        headers = http_headers(extra={'Content-Type': "application/json"})
        create_app_url = "https://api.plivo.com/v1/Account/%s/Application/" % auth_id

        response = requests.post(create_app_url, json=dict(app_name=app_name, answer_url=answer_url, message_url=message_url),
                                 headers=headers, auth=(auth_id, auth_token))

        if response.status_code in [201, 200, 202]:
            plivo_app_id = response.json()['app_id']
        else:  # pragma: no cover
            plivo_app_id = None

        plivo_config = {Channel.CONFIG_PLIVO_AUTH_ID: auth_id,
                        Channel.CONFIG_PLIVO_AUTH_TOKEN: auth_token,
                        Channel.CONFIG_PLIVO_APP_ID: plivo_app_id,
                        Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

        plivo_number = phone_number.strip('+ ').replace(' ', '')
        response = requests.get("https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number), headers=headers, auth=(auth_id, auth_token))

        if response.status_code != 200:
            response = requests.post("https://api.plivo.com/v1/Account/%s/PhoneNumber/%s/" % (auth_id, plivo_number), headers=headers, auth=(auth_id, auth_token))

            if response.status_code != 201:  # pragma: no cover
                raise Exception(_("There was a problem claiming that number, please check the balance on your account."))

            response = requests.get("https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number), headers=headers, auth=(auth_id, auth_token))

        if response.status_code == 200:
            response = requests.post("https://api.plivo.com/v1/Account/%s/Number/%s/" % (auth_id, plivo_number),
                                     json=dict(app_id=plivo_app_id), headers=headers, auth=(auth_id, auth_token))

            if response.status_code != 202:  # pragma: no cover
                raise Exception(_("There was a problem updating that number, please try again."))

        phone_number = '+' + plivo_number
        phone = phonenumbers.format_number(phonenumbers.parse(phone_number, None),
                                           phonenumbers.PhoneNumberFormat.NATIONAL)

        channel = Channel.create(org, user, country, 'PL', name=phone, address=phone_number,
                                 config=plivo_config, uuid=plivo_uuid)

        analytics.track(user.username, 'temba.channel_claim_plivo', dict(number=phone_number))

        return channel

    def remove_api_credentials_from_session(self):
        if Channel.CONFIG_PLIVO_AUTH_ID in self.request.session:
            del self.request.session[Channel.CONFIG_PLIVO_AUTH_ID]
        if Channel.CONFIG_PLIVO_AUTH_TOKEN in self.request.session:
            del self.request.session[Channel.CONFIG_PLIVO_AUTH_TOKEN]
