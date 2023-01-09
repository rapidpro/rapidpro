# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this channel will be used in"),
        )
        number = forms.CharField(
            max_length=14, min_length=4, label=_("Number"), help_text=_("The number you are connecting.")
        )
        username = forms.CharField(max_length=64, label=_("Username"), help_text=_("Your username on Bandwidth"))
        password = forms.CharField(max_length=64, label=_("Password"), help_text=_("Your password on Bandwidth"))
        account_id = forms.CharField(max_length=64, label=_("Account ID"), help_text=_("Your account ID on Bandwidth"))

    form_class = Form

    def form_valid(self, form):
        data = form.cleaned_data
        config = {
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
            "account_id": data["account_id"],
        }

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            data["country"],
            self.channel_type,
            name=f"Bandwidth: {data['number']}",
            address=data["number"],
            config=config,
        )

        return super().form_valid(form)
