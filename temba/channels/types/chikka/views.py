# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms

from django.utils.translation import ugettext_lazy as _

from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin, AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):
    class ChikkaForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=4, label=_("Number"),
                                 help_text=_("The short code you are connecting."))
        username = forms.CharField(label=_("Client Id"),
                                   help_text=_("The Client Id found on your Chikka API credentials page"))
        password = forms.CharField(label=_("Secret Key"),
                                   help_text=_("The Secret Key found on your Chikka API credentials page"))

    form_class = ChikkaForm

    def get_country(self, obj):
        return "Philippines"

    def get_submitted_country(self, data):
        return 'PH'
