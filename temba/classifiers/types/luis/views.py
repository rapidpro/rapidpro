import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.views import BaseConnectView


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        name = forms.CharField(help_text=_("The name of your LUIS app"))
        app_id = forms.CharField(help_text=_("The id for your LUIS app"))
        version = forms.CharField(help_text=_("The name of the version of your LUIS app to use"))
        primary_key = forms.CharField(help_text=_("The primary key for your LUIS app"))
        endpoint_url = forms.URLField(help_text=_("The endpoint URL for your LUIS app"))

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
            LuisType.CONFIG_VERSION: form.cleaned_data["version"],
            LuisType.CONFIG_PRIMARY_KEY: form.cleaned_data["primary_key"],
            LuisType.CONFIG_ENDPOINT_URL: form.cleaned_data["endpoint_url"],
        }

        self.object = Classifier.create(self.org, self.request.user, LuisType.slug, form.cleaned_data["name"], config)

        return super().form_valid(form)
