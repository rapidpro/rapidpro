# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import phonenumbers

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin, AuthenticatedExternalCallbackClaimView


class ClaimView(AuthenticatedExternalCallbackClaimView):
    class JasminForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=4, label=_("Number"),
                                 help_text=_("The short code or phone number you are connecting."))
        url = forms.URLField(label=_("URL"),
                             help_text=_("The URL for the Jasmin server send path. ex: https://jasmin.gateway.io/send"))
        username = forms.CharField(label=_("Username"),
                                   help_text=_("The username to be used to authenticate to Jasmin"))
        password = forms.CharField(label=_("Password"),
                                   help_text=_("The password to be used to authenticate to Jasmin"))

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

    form_class = JasminForm
