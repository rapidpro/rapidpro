import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        name = forms.CharField(help_text=_("Your app's name"))
        app_id = forms.IntegerField(label=_("App ID"), help_text=_("Your app's ID"))
        access_token = forms.CharField(help_text=_("Your app's server access token"))

        def clean(self):
            cleaned = super().clean()

            # only continue if base validation passed
            if not self.is_valid():
                return cleaned

            # try a basic call to see available entities
            response = requests.get(
                "https://api.wit.ai/entities", headers={"Authorization": f"Bearer {cleaned['access_token']}"}
            )

            if response.status_code != 200:
                raise forms.ValidationError(_("Unable to access wit.ai with credentials, please check and try again"))

            # make sure we have an intent entity, we can't classify without it
            response = requests.get(
                "https://api.wit.ai/entities/intent", headers={"Authorization": f"Bearer {cleaned['access_token']}"}
            )

            if response.status_code != 200:
                raise forms.ValidationError(
                    _("Unable to get intent entity, make sure you have at least one intent defined")
                )

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import WitType

        config = {
            WitType.CONFIG_ACCESS_TOKEN: form.cleaned_data["access_token"],
            WitType.CONFIG_APP_ID: str(form.cleaned_data["app_id"]),
        }

        self.object = Classifier.create(self.org, self.request.user, WitType.slug, form.cleaned_data["name"], config)

        return super().form_valid(form)
