# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import requests

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        page_access_token = forms.CharField(min_length=43, required=True,
                                            help_text=_("The Page Access Token for your Application"))

        def clean_page_access_token(self):
            value = self.cleaned_data['page_access_token']

            # hit the FB graph, see if we can load the page attributes
            response = requests.get('https://graph.facebook.com/v2.5/me', params={'access_token': value})
            response_json = response.json()
            if response.status_code != 200:
                default_error = _("Invalid page access token, please check it and try again.")
                raise ValidationError(response_json.get('error', default_error).get('message', default_error))

            self.cleaned_data['page'] = response_json
            return value

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        page = form.cleaned_data['page']
        auth_token = form.cleaned_data['page_access_token']

        config = {
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_PAGE_NAME: page['name'],
            Channel.CONFIG_SECRET: Channel.generate_secret()
        }
        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=page['name'], address=page['id'], config=config
        )

        return super(ClaimView, self).form_valid(form)
