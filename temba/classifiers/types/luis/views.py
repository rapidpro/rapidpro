import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.views import BaseConnectView
from temba.utils.fields import ExternalURLField


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        SLOT_CHOICES = (("staging", _("Staging")), ("production", _("Production")))

        name = forms.CharField(help_text=_("The name of your LUIS app"))
        app_id = forms.CharField(label=_("App ID"), help_text=_("The ID of your LUIS app"))
        subscription_key = forms.CharField(help_text=_("The subscription key"))
        endpoint_url = ExternalURLField(help_text=_("The endpoint URL"))
        slot = forms.ChoiceField(help_text=_("The publishing slot"), choices=SLOT_CHOICES)

        def clean(self):
            from .type import LuisType

            cleaned = super().clean()

            if not self.is_valid():
                return cleaned

            endpoint = cleaned["endpoint_url"]

            # try to look up intents
            response = requests.get(
                endpoint + "/apps/" + cleaned["app_id"] + "/versions/" + cleaned["version"] + "/intents",
                headers={LuisType.AUTH_HEADER: cleaned["primary_key"]},
            )

            if response.status_code != 200:
                raise forms.ValidationError(
                    _("Unable to get intents for your app, please check credentials and try again")
                )

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import LuisType

        config = {
            LuisType.CONFIG_APP_ID: form.cleaned_data["app_id"],
            LuisType.CONFIG_PRIMARY_KEY: form.cleaned_data["primary_key"],
            LuisType.CONFIG_ENDPOINT_URL: form.cleaned_data["endpoint_url"],
            LuisType.CONFIG_SLOT: form.cleaned_data["slot"],
        }

        self.object = Classifier.create(self.org, self.request.user, LuisType.slug, form.cleaned_data["name"], config)

        return super().form_valid(form)
