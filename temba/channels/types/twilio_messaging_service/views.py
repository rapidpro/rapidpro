from __future__ import unicode_literals, absolute_import

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from twilio import TwilioRestException

from temba.orgs.models import ACCOUNT_SID
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
                return HttpResponseRedirect(reverse('channels.channel_claim'))
            self.account = self.client.accounts.get(org.config_json()[ACCOUNT_SID])
        except TwilioRestException:
            return HttpResponseRedirect(reverse('channels.channel_claim'))

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

        config = dict(messaging_service_sid=data['messaging_service_sid'])

        self.object = Channel.create(org, user, data['country'], 'TMS',
                                     name=data['messaging_service_sid'], address=None, config=config)

        return super(ClaimView, self).form_valid(form)
