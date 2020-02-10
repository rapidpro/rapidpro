import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        name = forms.CharField(help_text=_("The name of your bot"))
        access_token = forms.CharField(help_text=_("Access token for your bot, leave out leading Bearer"))

        def clean(self):
            cleaned = super().clean()

            # only continue if base validation passed
            if not self.is_valid():
                return cleaned

            # try a basic call to see available entities
            response = requests.get(
                "https://nlp.bothub.it/info/", headers={"Authorization": f"Bearer {cleaned['access_token']}"}
            )

            if response.status_code != 200:
                raise forms.ValidationError(_("Unable to access bothub with credentials, please check and try again"))

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import BothubType

        config = {BothubType.CONFIG_ACCESS_TOKEN: form.cleaned_data["access_token"]}

        self.object = Classifier.create(
            self.org, self.request.user, BothubType.slug, form.cleaned_data["name"], config
        )

        return super().form_valid(form)
