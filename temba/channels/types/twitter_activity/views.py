# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import six

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from temba.utils.twitter import TembaTwython, TwythonError
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        api_key = forms.CharField(label=_('Consumer Key'))
        api_secret = forms.CharField(label=_('Consumer Secret'))
        access_token = forms.CharField(label=_('Access Token'))
        access_token_secret = forms.CharField(label=_('Access Token Secret'))
        env_name = forms.CharField(label=_('Environment Name'))

        def clean(self):
            cleaned_data = super(ClaimView.Form, self).clean()
            api_key = cleaned_data.get('api_key')
            api_secret = cleaned_data.get('api_secret')
            access_token = cleaned_data.get('access_token')
            access_token_secret = cleaned_data.get('access_token_secret')

            org = self.request.user.get_org()

            if api_key and api_secret and access_token and access_token_secret:
                twitter = TembaTwython(api_key, api_secret, access_token, access_token_secret)
                try:
                    user = twitter.verify_credentials()

                    # check there isn't already a channel for this Twitter account
                    if org.channels.filter(channel_type=self.channel_type.code,
                                           address=user['screen_name'], is_active=True).exists():
                        raise ValidationError(_("A Twitter channel already exists for that handle."))

                except TwythonError:
                    raise ValidationError(_("The provided Twitter credentials do not appear to be valid."))

            return cleaned_data

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        cleaned_data = form.cleaned_data
        api_key = cleaned_data['api_key']
        api_secret = cleaned_data['api_secret']
        access_token = cleaned_data['access_token']
        access_token_secret = cleaned_data['access_token_secret']
        env_name = cleaned_data['env_name']

        twitter = TembaTwython(api_key, api_secret, access_token, access_token_secret)
        account_info = twitter.verify_credentials()
        handle_id = six.text_type(account_info['id'])
        screen_name = account_info['screen_name']

        config = {
            'handle_id': handle_id,
            'api_key': api_key,
            'api_secret': api_secret,
            'access_token': access_token,
            'access_token_secret': access_token_secret,
            'env_name': env_name,
            Channel.CONFIG_CALLBACK_DOMAIN: settings.HOSTNAME,
        }

        self.object = Channel.create(org, self.request.user, None, self.channel_type, name="@%s" % screen_name,
                                     address=screen_name, config=config)

        return super(ClaimView, self).form_valid(form)
