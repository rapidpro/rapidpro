from __future__ import unicode_literals, absolute_import

import requests

from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.IntegerField(help_text=_("Your enterprise WhatsApp number"))
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        base_url = forms.URLField(help_text=_("The base URL for your WhatsApp enterprise installation"))
        username = forms.CharField(max_length=32,
                                   help_text=_("The username to access your WhatsApp enterprise account"))
        password = forms.CharField(max_length=64,
                                   help_text=_("The password to access your WhatsApp enterprise account"))

        def clean(self):
            try:
                resp = requests.post(self.cleaned_data['base_url'] + '/api/check_health.php',
                                     json=dict(payload=['gateway_status']),
                                     auth=(self.cleaned_data['username'], self.cleaned_data['password']))

                if resp.status_code != 200:
                    raise Exception("Received non-200 response: %d", resp.status_code)

            except Exception:
                raise forms.ValidationError("Unable to check WhatsApp enterprise account, please check username and password")

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = {
            Channel.CONFIG_BASE_URL: data['base_url'],
            Channel.CONFIG_USERNAME: data['username'],
            Channel.CONFIG_PASSWORD: data['password'],
        }

        self.object = Channel.create(org, user, data['country'], 'WA',
                                     name="WhatsApp: %s" % data['number'],
                                     address=data['number'],
                                     config=config)

        return super(ClaimView, self).form_valid(form)
