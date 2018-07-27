# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin, AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, min_length=4, label=_("Number"),
                                 help_text=("The shortcode or phone number you are connecting."))
        url = forms.URLField(label=_("URL"),
                             help_text=_(
                                 "The URL for the Junebug channel. ex: https://junebug.praekelt.org/jb/channels/3853bb51-d38a-4bca-b332-8a57c00f2a48/messages.json"))
        username = forms.CharField(label=_("Username"),
                                   help_text=_("The username to be used to authenticate to Junebug"),
                                   required=False)
        password = forms.CharField(label=_("Password"),
                                   help_text=_("The password to be used to authenticate to Junebug"),
                                   required=False)
        secret = forms.CharField(label=_("Secret"),
                                 help_text=_("The token Junebug should use to authenticate"),
                                 required=False)

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data
        config = {Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}
        if data['secret']:
            config[Channel.CONFIG_SECRET] = data['secret']

        self.object = Channel.add_authenticated_external_channel(org, self.request.user,
                                                                 self.get_submitted_country(data),
                                                                 data['number'], data['username'],
                                                                 data['password'], 'JN',
                                                                 data.get('url'),
                                                                 role=Channel.DEFAULT_ROLE,
                                                                 extra_config=config)

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
