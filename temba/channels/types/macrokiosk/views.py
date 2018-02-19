# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class MacrokioskClaimForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                 help_text=_("The phone number or short code you are connecting with country code. "
                                             "ex: +250788123124"))
        sender_id = forms.CharField(label=_("Sender ID"),
                                    help_text=_("The sender ID provided by Macrokiosk to use their API"))

        username = forms.CharField(label=_("Username"),
                                   help_text=_("The username provided by Macrokiosk to use their API"))
        password = forms.CharField(label=_("Password"),
                                   help_text=_("The password provided by Macrokiosk to use their API"))
        service_id = forms.CharField(label=_("Service ID"),
                                     help_text=_("The Service ID provided by Macrokiosk to use their API"))

    form_class = MacrokioskClaimForm

    def get_submitted_country(self, data):
        return data['country']

    def form_valid(self, form):
        org = self.request.user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = {
            Channel.CONFIG_USERNAME: data.get('username', None),
            Channel.CONFIG_PASSWORD: data.get('password', None),
            Channel.CONFIG_MACROKIOSK_SENDER_ID: data.get('sender_id', data['number']),
            Channel.CONFIG_MACROKIOSK_SERVICE_ID: data.get('service_id', None)
        }
        self.object = Channel.add_config_external_channel(org, self.request.user,
                                                          self.get_submitted_country(data),
                                                          data['number'], 'MK',
                                                          config,
                                                          role=Channel.ROLE_SEND + Channel.ROLE_RECEIVE)

        return super(ClaimView, self).form_valid(form)
