from __future__ import unicode_literals, absolute_import


from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class ZVClaimForm(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=6, min_length=1,
                                    help_text=_("The Zenvia short code"))
        account = forms.CharField(max_length=32,
                                  help_text=_("Your account name on Zenvia"))
        code = forms.CharField(max_length=64,
                               help_text=_("Your api code on Zenvia for authentication"))

    form_class = ZVClaimForm

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data
        phone = data['shortcode']
        account = data['account']
        code = data['code']

        config = dict(account=account, code=code)

        self.object = Channel.create(org, user, 'BR', 'ZV', name="Zenvia: %s" % phone, address=phone, config=config)

        return super(ClaimView, self).form_valid(form)
