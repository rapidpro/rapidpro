from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_("The phone number or short code you are connecting"),
        )
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        consumer_key = forms.CharField(required=True, help_text=_("The Consumer key"))
        consumer_secret = forms.CharField(required=True, help_text=_("The Consumer secret"))
        cp_address = forms.CharField(required=False, help_text=_("The CP Address (if provided by MTN)"))

    form_class = Form

    def form_valid(self, form):
        cleaned_data = form.cleaned_data

        country = cleaned_data["country"]
        number = cleaned_data["number"]
        config = {
            Channel.CONFIG_API_KEY: cleaned_data.get("consumer_key"),
            Channel.CONFIG_AUTH_TOKEN: cleaned_data.get("consumer_secret"),
        }

        if cleaned_data.get("cp_address"):
            config[self.channel_type.CP_ADDRESS] = cleaned_data.get("cp_address")

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            country,
            self.channel_type,
            name="",
            address=number,
            config=config,
            schemes=("tel",),
        )

        return super(ClaimView, self).form_valid(form)
