from django import forms
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, AuthenticatedExternalClaimView, ClaimViewMixin
from temba.utils.fields import ExternalURLField, SelectWidget


class ClaimView(AuthenticatedExternalClaimView):
    class ShaqodoonForm(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        number = forms.CharField(
            max_length=14, min_length=1, label=_("Number"), help_text=_("The short code you are connecting with.")
        )
        url = ExternalURLField(label=_("URL"), help_text=_("The URL provided to deliver messages"))
        username = forms.CharField(label=_("Username"), help_text=_("The username provided to use their API"))
        password = forms.CharField(label=_("Password"), help_text=_("The password provided to use their API"))

    form_class = ShaqodoonForm

    def get_country(self, obj):
        return "Somalia"

    def get_submitted_country(self, data):  # pragma: needs cover
        return "SO"

    def form_valid(self, form):
        data = form.cleaned_data
        self.object = Channel.add_config_external_channel(
            self.request.org,
            self.request.user,
            "SO",
            data["number"],
            self.channel_type,
            dict(send_url=data["url"], username=data["username"], password=data["password"]),
            tps=5,
        )

        return super(AuthenticatedExternalClaimView, self).form_valid(form)
