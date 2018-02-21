# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from uuid import uuid4
from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class KannelClaimForm(ClaimViewMixin.Form):
        number = forms.CharField(max_length=14, min_length=1, label=_("Number"),
                                 help_text=_("The phone number or short code you are connecting"))
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        url = forms.URLField(max_length=1024, label=_("Send URL"),
                             help_text=_("The publicly accessible URL for your Kannel instance for sending. "
                                         "ex: https://kannel.macklemore.co/cgi-bin/sendsms"))
        username = forms.CharField(max_length=64, required=False,
                                   help_text=_("The username to use to authenticate to Kannel, if left blank we "
                                               "will generate one for you"))
        password = forms.CharField(max_length=64, required=False,
                                   help_text=_("The password to use to authenticate to Kannel, if left blank we "
                                               "will generate one for you"))
        encoding = forms.ChoiceField(Channel.ENCODING_CHOICES, label=_("Encoding"),
                                     help_text=_("What encoding to use for outgoing messages"))
        verify_ssl = forms.BooleanField(initial=True, required=False, label=_("Verify SSL"),
                                        help_text=_("Whether to verify the SSL connection (recommended)"))
        use_national = forms.BooleanField(initial=False, required=False, label=_("Use National Numbers"),
                                          help_text=_("Use only the national number (no country code) when "
                                                      "sending (not recommended)"))

    form_class = KannelClaimForm

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data

        country = data['country']
        url = data['url']
        number = data['number']
        role = Channel.ROLE_SEND + Channel.ROLE_RECEIVE

        config = {Channel.CONFIG_SEND_URL: url,
                  Channel.CONFIG_VERIFY_SSL: data.get('verify_ssl', False),
                  Channel.CONFIG_USE_NATIONAL: data.get('use_national', False),
                  Channel.CONFIG_USERNAME: data.get('username', None),
                  Channel.CONFIG_PASSWORD: data.get('password', None),
                  Channel.CONFIG_ENCODING: data.get('encoding', Channel.ENCODING_DEFAULT),
                  Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}
        self.object = Channel.add_config_external_channel(org, self.request.user, country, number, 'KN',
                                                          config, role=role, parent=None)

        # if they didn't set a username or password, generate them, we do this after the addition above
        # because we use the channel id in the configuration
        config = self.object.config
        if not config.get(Channel.CONFIG_USERNAME, None):
            config[Channel.CONFIG_USERNAME] = '%s_%d' % (self.request.branding['name'].lower(), self.object.pk)

        if not config.get(Channel.CONFIG_PASSWORD, None):
            config[Channel.CONFIG_PASSWORD] = str(uuid4())

        self.object.config = config
        self.object.save()

        return super(ClaimView, self).form_valid(form)
