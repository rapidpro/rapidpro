import phonenumbers
from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import AuthenticatedExternalClaimView, ClaimViewMixin, ALL_COUNTRIES
from temba.utils.fields import SelectWidget, ExternalURLField


class ClaimView(AuthenticatedExternalClaimView):
    class InfoBipForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        number = forms.CharField(
            max_length=14,
            min_length=1,
            label=_("Number"),
            help_text=_("The phone number or short code you are connecting with country code. ex: +250788123124"),
        )

        url = ExternalURLField(label=_("URL"), help_text=_("The URL provided by the provider to use their API"))
        api_key = forms.CharField(
            label=_("API Key"), help_text=_("The API Key provided by the provider to use their API")
        )

    form_class = InfoBipForm

    def get_channel_config(self, org, data):
        return {Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

    def form_valid(self, form):
        org = self.request.user.get_org()

        data = form.cleaned_data
        phone_number = data["number"]
        try:
            parsed = phonenumbers.parse(phone_number, None)
            phone = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.INTERNATIONAL)
        except Exception:
            # this is a shortcode, just use it plain
            phone = phone_number

        self.object = Channel.add_config_external_channel(
            org,
            self.request.user,
            self.get_submitted_country(data),
            phone,
            self.channel_type,
            dict(send_url=data["url"], api_key=data["api_key"]),
        )

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
