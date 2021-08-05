import requests

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.classifiers.models import Classifier
from temba.classifiers.views import BaseConnectView
from temba.utils.fields import ExternalURLField

from .client import AuthoringClient, PredictionClient


class ConnectView(BaseConnectView):
    class Form(forms.Form):
        SLOT_CHOICES = (("staging", _("Staging")), ("production", _("Production")))

        name = forms.CharField(help_text=_("The name of your LUIS app"))
        app_id = forms.CharField(label=_("App ID"), help_text=_("The ID of your LUIS app"))
        authoring_endpoint = ExternalURLField(help_text=_("The authoring resource endpoint URL"))
        authoring_key = forms.CharField(help_text=_("The authoring resource access key"))
        prediction_endpoint = ExternalURLField(help_text=_("The prediction resource endpoint URL"))
        prediction_key = forms.CharField(help_text=_("The prediction resource access key"))
        slot = forms.ChoiceField(
            help_text=_("The slot where the prediction resource has been published"), choices=SLOT_CHOICES
        )

        def clean(self):
            cleaned = super().clean()

            if not self.is_valid():
                return cleaned

            # first check authoring credentials work
            try:
                client = AuthoringClient(cleaned["authoring_endpoint"], cleaned["authoring_key"])
                app_info = client.get_app(cleaned["app_id"])
                app_endpoints = app_info["endpoints"]
                if cleaned["slot"].upper() not in app_endpoints:
                    raise forms.ValidationError(_("App has not yet been published to %s slot.") % cleaned["slot"])
            except requests.RequestException as e:
                raise forms.ValidationError(_("Check authoring credentials: %s") % str(e))

            # then check prediction credentials work
            try:
                client = PredictionClient(cleaned["prediction_endpoint"], cleaned["prediction_key"])
                client.predict(cleaned["app_id"], cleaned["slot"], "test")
            except requests.RequestException as e:
                raise forms.ValidationError(_("Check prediction credentials: %s") % str(e))

            return cleaned

    form_class = Form

    def form_valid(self, form):
        from .type import LuisType

        config = {
            LuisType.CONFIG_APP_ID: form.cleaned_data["app_id"],
            LuisType.CONFIG_AUTHORING_ENDPOINT: form.cleaned_data["authoring_endpoint"],
            LuisType.CONFIG_AUTHORING_KEY: form.cleaned_data["authoring_key"],
            LuisType.CONFIG_PREDICTION_ENDPOINT: form.cleaned_data["prediction_endpoint"],
            LuisType.CONFIG_PREDICTION_KEY: form.cleaned_data["prediction_key"],
            LuisType.CONFIG_SLOT: form.cleaned_data["slot"],
        }

        self.object = Classifier.create(self.org, self.request.user, LuisType.slug, form.cleaned_data["name"], config)

        return super().form_valid(form)
