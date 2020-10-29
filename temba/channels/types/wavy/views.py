from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ALL_COUNTRIES, ClaimViewMixin
from temba.contacts.models import URN
from temba.utils.fields import SelectWidget


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES,
            widget=SelectWidget(attrs={"searchable": True}),
            label=_("Country"),
            help_text=_("The country this channel will be used in"),
        )

        number = forms.CharField(
            max_length=14, min_length=4, label=_("Number"), help_text=_("The number you are connecting.")
        )
        username = forms.CharField(label=_("Username"), help_text=_("The username for your Movile/Wavy account"))
        token = forms.CharField(
            label=_("Authentication Token"), help_text=_("The Authentication Token for your Movile/Wavy account")
        )

    form_class = Form

    def get_country(self, obj):
        return "Brazil"

    def form_valid(self, form):
        org = self.request.user.get_org()
        data = form.cleaned_data
        config = {Channel.CONFIG_USERNAME: data["username"], Channel.CONFIG_AUTH_TOKEN: data["token"]}

        self.object = Channel.create(
            org=org,
            user=self.request.user,
            country=data["country"],
            channel_type=self.channel_type,
            name=data["number"],
            address=data["number"],
            config=config,
            schemes=[URN.TEL_SCHEME],
        )

        return super(ClaimViewMixin, self).form_valid(form)
