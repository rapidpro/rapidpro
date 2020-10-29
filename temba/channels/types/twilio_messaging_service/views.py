from smartmin.views import SmartFormView
from twilio.base.exceptions import TwilioRestException

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import Org
from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import TWILIO_SUPPORTED_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class TwilioMessagingServiceForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=TWILIO_SUPPORTED_COUNTRIES, widget=SelectWidget(attrs={"searchable": True}),
        )
        messaging_service_sid = forms.CharField(
            label=_("Messaging Service SID"), help_text=_("The Twilio Messaging Service SID")
        )

    form_class = TwilioMessagingServiceForm

    def __init__(self, channel_type):
        super().__init__(channel_type)
        self.account = None
        self.client = None
        self.object = None

    def pre_process(self, *args, **kwargs):
        org = self.request.user.get_org()
        try:
            self.client = org.get_twilio_client()
            if not self.client:
                return HttpResponseRedirect(
                    f'{reverse("orgs.org_twilio_connect")}?claim_type={self.channel_type.slug}'
                )
            self.account = self.client.api.account.fetch()
        except TwilioRestException:
            return HttpResponseRedirect(f'{reverse("orgs.org_twilio_connect")}?claim_type={self.channel_type.slug}')

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["account_trial"] = self.account.type.lower() == "trial"
        return context

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        data = form.cleaned_data

        org_config = org.config
        config = {
            Channel.CONFIG_MESSAGING_SERVICE_SID: data["messaging_service_sid"],
            Channel.CONFIG_ACCOUNT_SID: org_config[Org.CONFIG_TWILIO_SID],
            Channel.CONFIG_AUTH_TOKEN: org_config[Org.CONFIG_TWILIO_TOKEN],
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

        return super().form_valid(form)
