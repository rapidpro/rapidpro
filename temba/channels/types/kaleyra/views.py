from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(help_text=_("Your enterprise WhatsApp number"))
        country = forms.ChoiceField(choices=ALL_COUNTRIES, help_text=_("The country this phone number is used in"))
        account_sid = forms.CharField(label=_("Account SID"), help_text=_("Your Kaleyra Account SID"))
        api_key = forms.CharField(label=_("API Key"), help_text=_("Your Kaleyra API Key"))

        def clean_number(self):
            # check that our phone number looks sane
            country = self.data["country"]
            number = URN.normalize_number(self.data["number"], country)
            if not URN.validate(URN.from_parts(URN.TEL_SCHEME, number), country):
                raise forms.ValidationError(_("Please enter a valid phone number"))
            return number

    form_class = Form

    def form_valid(self, form):
        from .type import CONFIG_ACCOUNT_SID, CONFIG_API_KEY

        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = {
            CONFIG_ACCOUNT_SID: data["account_sid"],
            CONFIG_API_KEY: data["api_key"],
        }
        self.object = Channel.create(
            org,
            user,
            data["country"],
            "KWA",
            name="Kaleyra WhatsApp: %s" % data["number"],
            address=data["number"],
            config=config,
            tps=45,
        )

        return super().form_valid(form)
