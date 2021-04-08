from smartmin.views import SmartFormView

from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import InputWidget

from ...views import IntegrationManagementViewMixin
from .client import DTOneClient


class AccountView(IntegrationManagementViewMixin, SmartFormView):
    class Form(IntegrationManagementViewMixin.Form):
        api_key = forms.CharField(label=_("API Key"), required=False, widget=InputWidget())
        api_secret = forms.CharField(label=_("API Secret"), required=False, widget=InputWidget())
        disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=False)

        def clean(self):
            cleaned_data = super().clean()

            if cleaned_data["disconnect"] != "true":
                api_key = cleaned_data.get("api_key")
                api_secret = cleaned_data.get("api_secret")
                client = DTOneClient(api_key, api_secret)

                try:
                    client.get_balances()
                except DTOneClient.Exception:
                    raise forms.ValidationError(
                        _("Your DT One API key and secret seem invalid. Please check them again and retry.")
                    )

    form_class = Form
    submit_button_name = "Save"
    success_message = ""
    success_url = "@orgs.org_home"

    def derive_initial(self):
        initial = super().derive_initial()
        config = self.request.user.get_org().config
        initial["api_key"] = config.get(self.integration_type.CONFIG_KEY)
        initial["api_secret"] = config.get(self.integration_type.CONFIG_SECRET)
        initial["disconnect"] = "false"
        return initial

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()
        disconnect = form.cleaned_data.get("disconnect", "false") == "true"
        if disconnect:
            self.integration_type.disconnect(org, user)
            return HttpResponseRedirect(reverse("orgs.org_home"))
        else:
            self.integration_type.connect(org, user, form.cleaned_data["api_key"], form.cleaned_data["api_secret"])
            return super().form_valid(form)
