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

        def clean_number(self):
            number = self.data["number"]

            # number is a shortcode, accept as is
            if len(number) > 0 and len(number) < 7:
                return number

            # otherwise, try to parse into an international format
            if number and number[0] != "+":
                number = "+" + number

            try:
                cleaned = phonenumbers.parse(number, None)
                return phonenumbers.format_number(cleaned, phonenumbers.PhoneNumberFormat.E164)
            except Exception:  # pragma: needs cover
                raise forms.ValidationError(
                    _("Invalid phone number, please include the country code. ex: +250788123123")
                )

    form_class = InfoBipForm

    def get_channel_config(self, org, data):
        return {Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

    def form_valid(self, form):
        org = self.request.user.get_org()

        data = form.cleaned_data
        self.object = Channel.add_config_external_channel(
            org,
            self.request.user,
            self.get_submitted_country(data),
            data["number"],
            self.channel_type,
            dict(send_url=data["url"], api_key=data["api_key"]),
        )

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
