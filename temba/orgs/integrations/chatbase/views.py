from django import forms
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from ...views import IntegrationFormaxView


class AccountView(IntegrationFormaxView):
    class Form(IntegrationFormaxView.Form):
        agent_name = forms.CharField(
            max_length=255, label=_("Agent Name"), required=False, help_text=_("Your Chatbase agent's name.")
        )
        api_key = forms.CharField(
            max_length=255, label=_("API Key"), required=False, help_text=_("The chatbase agent's API Key.")
        )
        version = forms.CharField(max_length=10, label=_("Version"), required=False)
        disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=True)

        def clean(self):
            super().clean()
            if self.cleaned_data.get("disconnect", "false") == "false":
                agent_name = self.cleaned_data.get("agent_name")
                api_key = self.cleaned_data.get("api_key")

                if not agent_name or not api_key:
                    raise forms.ValidationError(_("Missing agent name or API key."))

            return self.cleaned_data

    form_class = Form
    template_name = "orgs/integrations/chatbase/account.haml"

    def derive_initial(self):
        initial = super().derive_initial()
        config = self.request.user.get_org().config
        initial["agent_name"] = config.get(self.integration_type.CONFIG_AGENT_NAME)
        initial["api_key"] = config.get(self.integration_type.CONFIG_API_KEY)
        initial["version"] = config.get(self.integration_type.CONFIG_VERSION)
        initial["disconnect"] = "false"
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        config = self.request.user.get_org().config
        context["agent_name"] = config.get(self.integration_type.CONFIG_AGENT_NAME)
        return context

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()
        disconnect = form.cleaned_data.get("disconnect", "false") == "true"
        if disconnect:
            self.integration_type.disconnect(org, user)
            return HttpResponseRedirect(reverse("orgs.org_home"))
        else:
            self.integration_type.connect(
                org, user, form.cleaned_data["agent_name"], form.cleaned_data["api_key"], form.cleaned_data["version"]
            )
            return super().form_valid(form)
