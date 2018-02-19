# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals


from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class ZVClaimForm(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=6, min_length=1,
                                    help_text=_("The Zenvia short code"))
        username = forms.CharField(max_length=32,
                                   help_text=_("The account username provided by Zenvia"))
        password = forms.CharField(max_length=64,
                                   help_text=_("The account password provided by Zenvia"))

    form_class = ZVClaimForm

    def form_valid(self, form):
        user = self.request.user
        data = form.cleaned_data
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        config = {
            Channel.CONFIG_USERNAME: data['username'],
            Channel.CONFIG_PASSWORD: data['password'],
        }

        self.object = Channel.create(org, user, 'BR', 'ZV', name="Zenvia: %s" % data['shortcode'],
                                     address=data['shortcode'], config=config)

        return super(ClaimView, self).form_valid(form)
