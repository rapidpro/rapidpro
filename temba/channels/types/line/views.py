# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import requests

from django import forms
from django.core.exceptions import ValidationError
from django.db.models.query import Q
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from temba.utils.http import http_headers
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        access_token = forms.CharField(label=_("Access Token"), required=True,
                                       help_text=_("The Access Token of the LINE Bot"))
        secret = forms.CharField(label=_("Secret"), required=True, help_text=_("The Secret of the LINE Bot"))

        def clean(self):
            access_token = self.cleaned_data.get('access_token')
            secret = self.cleaned_data.get('secret')

            headers = http_headers(extra={
                'Content-Type': 'application/json',
                'Authorization': 'Bearer %s' % access_token
            })

            response = requests.get('https://api.line.me/v1/oauth/verify', headers=headers)
            content = response.json()

            if response.status_code != 200:
                raise ValidationError(content.get('error_desciption'))
            else:
                channel_id = content.get('channelId')
                channel_mid = content.get('mid')

                credentials = {
                    'channel_id': channel_id,
                    'channel_mid': channel_mid,
                    'channel_access_token': access_token,
                    'channel_secret': secret
                }

                existing = Channel.objects.filter(
                    Q(config__contains=channel_id) | Q(config__contains=secret) | Q(
                        config__contains=access_token), channel_type=self.channel_type.code, address=channel_mid,
                    is_active=True).first()
                if existing:
                    raise ValidationError(_("A channel with this configuration already exists."))

                headers.pop('Content-Type')
                response_profile = requests.get('https://api.line.me/v1/profile', headers=headers)
                content_profile = json.loads(response_profile.content)

                credentials['profile'] = {
                    'picture_url': content_profile.get('pictureUrl'),
                    'display_name': content_profile.get('displayName')
                }

                return credentials

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        profile = form.cleaned_data.get('profile')
        credentials = form.cleaned_data
        credentials.pop('profile')

        channel_id = credentials.get('channel_id')
        channel_secret = credentials.get('channel_secret')
        channel_mid = credentials.get('channel_mid')
        channel_access_token = credentials.get('channel_access_token')

        config = {
            'auth_token': channel_access_token,
            'secret': channel_secret,
            'channel_id': channel_id,
            'channel_mid': channel_mid
        }

        self.object = Channel.create(org, self.request.user, None, self.channel_type,
                                     name=profile.get('display_name'), address=channel_mid, config=config)

        return super(ClaimView, self).form_valid(form)
