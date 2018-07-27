from __future__ import unicode_literals, absolute_import

import phonenumbers
from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class VerboiceClaimForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                 help_text=_("The phone number with country code or short code you are connecting. "
                                             "ex: +250788123124 or 15543"))
        username = forms.CharField(label=_("Username"),
                                   help_text=_("The username provided by the provider to use their API"))
        password = forms.CharField(label=_("Password"),
                                   help_text=_("The password provided by the provider to use their API"))
        channel = forms.CharField(label=_("Channel Name"),
                                  help_text=_("The Verboice channel that will be handling your calls"))

        def clean_number(self):
            number = self.data['number']

            # number is a shortcode, accept as is
            if len(number) > 0 and len(number) < 7:
                return number

            # otherwise, try to parse into an international format
            if number and number[0] != '+':
                number = '+' + number

            try:
                cleaned = phonenumbers.parse(number, None)
                return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
            except Exception:  # pragma: needs cover
                raise forms.ValidationError(
                    _("Invalid phone number, please include the country code. ex: +250788123123"))

    form_class = VerboiceClaimForm

    def form_valid(self, form):
        org = self.request.user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data
        self.object = Channel.add_config_external_channel(org, self.request.user, data['country'],
                                                          data['number'], "VB",
                                                          dict(username=data['username'],
                                                               password=data['password'],
                                                               channel=data['channel']),
                                                          role=Channel.ROLE_CALL + Channel.ROLE_ANSWER)
        return super(ClaimView, self).form_valid(form)
