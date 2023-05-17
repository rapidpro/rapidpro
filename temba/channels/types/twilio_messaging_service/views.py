from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioRestException
from twilio.rest import Client as TwilioClient

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.types.twilio.views import COUNTRY_CHOICES
from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class TwilioMessagingServiceForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=COUNTRY_CHOICES, widget=SelectWidget(attrs={"searchable": True}))
        messaging_service_sid = forms.CharField(
            label=_("Messaging Service SID"), help_text=_("The Twilio Messaging Service SID")
        )

    form_class = TwilioMessagingServiceForm

    def __init__(self, channel_type):
        super().__init__(channel_type)
        self.account = None
        self.client = None
        self.object = None

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

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        account_trial = False
        if self.account:
            account_trial = self.account.type.lower() == "trial"

        context["account_trial"] = account_trial

        context["current_creds_account"] = self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID, None)

        return context

    def form_valid(self, form):
        user = self.request.user
        org = self.request.org
        data = form.cleaned_data

        config = {
            self.channel_type.CONFIG_MESSAGING_SERVICE_SID: data["messaging_service_sid"],
            Channel.CONFIG_ACCOUNT_SID: self.request.session.get(self.channel_type.SESSION_ACCOUNT_SID),
            Channel.CONFIG_AUTH_TOKEN: self.request.session.get(self.channel_type.SESSION_AUTH_TOKEN),
            Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain(),
        }

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name=data["messaging_service_sid"],
            address=None,
            config=config,
        )
        del self.request.session[self.channel_type.SESSION_ACCOUNT_SID]
        del self.request.session[self.channel_type.SESSION_AUTH_TOKEN]

        return super().form_valid(form)
