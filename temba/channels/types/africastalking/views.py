# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=6, min_length=1,
                                    help_text=_("Your short code on Africa's Talking"))
        country = forms.ChoiceField(choices=(('KE', _("Kenya")), ('UG', _("Uganda")), ('MW', _("Malawi"))))
        is_shared = forms.BooleanField(initial=False, required=False,
                                       help_text=_("Whether this short code is shared with others"))
        username = forms.CharField(max_length=32,
                                   help_text=_("Your username on Africa's Talking"))
        api_key = forms.CharField(max_length=64,
                                  help_text=_("Your api key, should be 64 characters"))

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = dict(username=data['username'], api_key=data['api_key'], is_shared=data['is_shared'])

        self.object = Channel.create(org, user, data['country'], 'AT',
                                     name="Africa's Talking: %s" % data['shortcode'], address=data['shortcode'],
                                     config=config)

        return super(ClaimView, self).form_valid(form)
