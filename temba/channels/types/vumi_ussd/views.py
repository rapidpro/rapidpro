from __future__ import unicode_literals, absolute_import

from uuid import uuid4

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin, AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):
    class Form(ClaimViewMixin.Form):
            country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                        help_text=_("The country this phone number is used in"))
            number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                     help_text=_("The phone number with country code or short code you are connecting. ex: +250788123124 or 15543"))
            account_key = forms.CharField(label=_("Account Key"),
                                          help_text=_("Your Vumi account key as found under Account -> Details"))
            conversation_key = forms.CharField(label=_("Conversation Key"),
                                               help_text=_("The key for your Vumi conversation, can be found in the URL"))
            api_url = forms.URLField(label=_("API URL"), required=False,
                                     help_text=_("Custom VUMI API Endpoint. Leave blank to use default of: '%s'" % Channel.VUMI_GO_API_URL))

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data
        if not data.get('api_url'):
            api_url = Channel.VUMI_GO_API_URL
        else:
            api_url = data.get('api_url')

        self.object = Channel.add_config_external_channel(org, self.request.user,
                                                          data['country'], data['number'], 'VMU',
                                                          dict(account_key=data['account_key'],
                                                               access_token=str(uuid4()),
                                                               conversation_key=data['conversation_key'],
                                                               api_url=api_url),
                                                          role=Channel.ROLE_USSD)

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
