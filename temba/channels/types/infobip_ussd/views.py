from __future__ import unicode_literals, absolute_import

from django import forms
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartFormView
from ...models import Channel
from ...views import ClaimViewMixin, ALL_COUNTRIES


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(choices=ALL_COUNTRIES, label=_("Country"),
                                    help_text=_("The country this phone number is used in"))
        number = forms.CharField(max_length=14, required=False, label=_("Number"),
                                 help_text=_(
                                     "The short code you are connecting. ex: *111#"))

    title = _("Connect Infobip USSD")
    form_class = Form
    success_url = "id@channels.channel_configuration"

    def form_valid(self, form):
        org = self.request.user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        self.object = Channel.add_config_external_channel(org, self.request.user,
                                                          data['country'], data['number'], self.channel_type,
                                                          config=dict(sync_handling=True),
                                                          role=Channel.ROLE_USSD)

        return super(ClaimView, self).form_valid(form)
