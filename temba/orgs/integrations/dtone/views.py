from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from ...views import IntegrationFormaxView
from .client import DTOneClient


class AccountView(IntegrationFormaxView):
    class Form(IntegrationFormaxView.Form):
        api_key = forms.CharField(label=_("API Key"), required=False)
        api_secret = forms.CharField(label=_("API Secret"), required=False)
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
    template_name = "orgs/integrations/dtone/account.haml"

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
