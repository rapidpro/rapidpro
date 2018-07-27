# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import phonenumbers

from uuid import uuid4
from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView

from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN
from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class TwimlApiClaimForm(ClaimViewMixin.Form):
        ROLES = (
            (Channel.ROLE_SEND + Channel.ROLE_RECEIVE, _('Messaging')),
            (Channel.ROLE_CALL + Channel.ROLE_ANSWER, _('Voice')),
            (Channel.ROLE_SEND + Channel.ROLE_RECEIVE + Channel.ROLE_CALL + Channel.ROLE_ANSWER, _('Both')),
        )
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                 help_text=_("The phone number without country code or short code you are connecting."))
        url = forms.URLField(max_length=1024, label=_("TwiML REST API Host"), help_text=_(
            "The publicly accessible URL for your TwiML REST API instance ex: https://api.twilio.com"))
        role = forms.ChoiceField(choices=ROLES, label=_("Role"),
                                 help_text=_("Choose the role that this channel supports"))
        account_sid = forms.CharField(max_length=64, required=False,
                                      help_text=_("The Account SID to use to authenticate to the TwiML REST API"),
                                      widget=forms.TextInput(attrs={'autocomplete': 'off'}))
        account_token = forms.CharField(max_length=64, required=False,
                                        help_text=_("The Account Token to use to authenticate to the TwiML REST API"),
                                        widget=forms.TextInput(attrs={'autocomplete': 'off'}))

    form_class = TwimlApiClaimForm

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()
        data = form.cleaned_data

        country = data.get('country')
        number = data.get('number')
        url = data.get('url')
        role = data.get('role')

        config = {Channel.CONFIG_SEND_URL: url,
                  ACCOUNT_SID: data.get('account_sid', None),
                  ACCOUNT_TOKEN: data.get('account_token', None),
                  Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

        is_short_code = len(number) <= 6

        if not is_short_code:
            phone_number = phonenumbers.parse(number=number, region=country)
            number = "{0}{1}".format(str(phone_number.country_code), str(phone_number.national_number))

        address = number

        is_short_code = len(address) <= 6

        name = address

        if is_short_code:
            role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE
        else:
            address = "+%s" % address
            name = phonenumbers.format_number(phonenumbers.parse(address, None), phonenumbers.PhoneNumberFormat.NATIONAL)

        existing = Channel.objects.filter(address=address, org=org, channel_type='TW').first()
        if existing:
            existing.name = name
            existing.address = address
            existing.config = config
            existing.country = country
            existing.role = role
            existing.save()
            self.object = existing
        else:
            self.object = Channel.create(org, user, country, 'TW', name=name, address=address, config=config, role=role)

        # if they didn't set a username or password, generate them, we do this after the addition above
        # because we use the channel id in the configuration
        config = self.object.config
        if not config.get(ACCOUNT_SID, None):  # pragma: needs cover
            config[ACCOUNT_SID] = '%s_%d' % (self.request.branding['name'].lower(), self.object.pk)

        if not config.get(ACCOUNT_TOKEN, None):  # pragma: needs cover
            config[ACCOUNT_TOKEN] = str(uuid4())

        self.object.config = config
        self.object.save()

        return super(ClaimView, self).form_valid(form)
