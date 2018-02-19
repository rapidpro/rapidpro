# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        app_id = forms.CharField(min_length=32, required=True, help_text=_("The Jiochat App ID"))
        app_secret = forms.CharField(min_length=32, required=True, help_text=_("The Jiochat App secret"))

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        cleaned_data = form.cleaned_data

        config = {
            'jiochat_app_id': cleaned_data.get('app_id'),
            'jiochat_app_secret': cleaned_data.get('app_secret'),
            'secret': Channel.generate_secret(32),
        }

        self.object = Channel.create(org, self.request.user, None, self.channel_type, name='', address='', config=config)

        return super(ClaimView, self).form_valid(form)
