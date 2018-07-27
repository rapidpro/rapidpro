# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from twilio import TwilioRestException

from temba.orgs.models import ACCOUNT_SID, ACCOUNT_TOKEN
from ...models import Channel
from ...views import ClaimViewMixin, TWILIO_SUPPORTED_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class TwilioMessagingServiceForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=TWILIO_SUPPORTED_COUNTRIES)
        messaging_service_sid = forms.CharField(label=_("Messaging Service SID"),
                                                help_text=_("The Twilio Messaging Service SID"))

    form_class = TwilioMessagingServiceForm

    def __init__(self, channel_type):
        super(ClaimView, self).__init__(channel_type)
        self.account = None
        self.client = None
        self.object = None

    def pre_process(self, *args, **kwargs):
        org = self.request.user.get_org()
        try:
            self.client = org.get_twilio_client()
            if not self.client:
                return HttpResponseRedirect(reverse('orgs.org_twilio_connect'))
            self.account = self.client.accounts.get(org.config[ACCOUNT_SID])
        except TwilioRestException:
            return HttpResponseRedirect(reverse('orgs.org_twilio_connect'))

    def get_context_data(self, **kwargs):
        context = super(ClaimView, self).get_context_data(**kwargs)
        context['account_trial'] = self.account.type.lower() == 'trial'
        return context

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        org_config = org.config
        config = {Channel.CONFIG_MESSAGING_SERVICE_SID: data['messaging_service_sid'],
                  Channel.CONFIG_ACCOUNT_SID: org_config[ACCOUNT_SID],
                  Channel.CONFIG_AUTH_TOKEN: org_config[ACCOUNT_TOKEN],
                  Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

        self.object = Channel.create(org, user, data['country'], 'TMS',
                                     name=data['messaging_service_sid'], address=None, config=config)

        return super(ClaimView, self).form_valid(form)
