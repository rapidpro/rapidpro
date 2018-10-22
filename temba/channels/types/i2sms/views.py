
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(max_length=12, min_length=1, help_text=_("Your number or short code"))
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            label=_("Country"),
            required=False,
            help_text=_("The country this number is used in"),
        )
        channel_hash = forms.CharField(max_length=42, help_text=_("The hash of your i2SMS channel"))
        username = forms.CharField(max_length=32, help_text=_("Your i2SMS username"))
        password = forms.CharField(max_length=32, help_text=_("Your i2SMS password"))

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = dict(username=data["username"], password=data["password"], channel_hash=data["channel_hash"])

        self.object = Channel.create(org, user, data["country"], "I2", address=data["number"], config=config)

        return super().form_valid(form)
