# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin, AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):
    class ShaqodoonForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                 help_text=_("The short code you are connecting with."))
        url = forms.URLField(label=_("URL"),
                             help_text=_("The url provided to deliver messages"))
        username = forms.CharField(label=_("Username"),
                                   help_text=_("The username provided to use their API"))
        password = forms.CharField(label=_("Password"),
                                   help_text=_("The password provided to use their API"))

    form_class = ShaqodoonForm

    def get_country(self, obj):
        return "Somalia"

    def get_submitted_country(self, data):  # pragma: needs cover
        return 'SO'

    def form_valid(self, form):
        org = self.request.user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data
        self.object = Channel.add_config_external_channel(org, self.request.user,
                                                          'SO', data['number'], 'SQ',
                                                          dict(send_url=data['url'],
                                                               username=data['username'],
                                                               password=data['password']))

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
