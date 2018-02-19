# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _

from smartmin.views import SmartFormView
from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin
from temba.contacts.models import TEL_SCHEME


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES, label=_("Country"),
            help_text=_("The country this channel will be used in")
        )
        shortcode = forms.CharField(
            label=_("Short Code"),
            help_text=_("The short code you are connecting"),
        )
        service_id = forms.CharField(
            label=_("Service ID"),
            help_text=_("The service ID as provided by ArabiaCell")
        )
        charging_level = forms.ChoiceField(
            choices=(("0", _("Free")), ("1", _("Billed"))),
            help_text=_("The charging level for your account")
        )
        username = forms.CharField(
            label=_("Username"),
            help_text=_("The username for your API account")
        )
        password = forms.CharField(
            label=_("Password"),
            help_text=_("The password for your API account")
        )

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data
        config = {
            Channel.CONFIG_USERNAME: data['username'],
            Channel.CONFIG_PASSWORD: data['password'],
            "service_id": data['service_id'],
            "charging_level": data['charging_level'],
        }

        self.object = Channel.create(
            org=org, user=self.request.user, country=data['country'],
            channel_type='AC', name=data['shortcode'], address=data['shortcode'],
            config=config, schemes=[TEL_SCHEME]
        )

        return super(ClaimViewMixin, self).form_valid(form)
