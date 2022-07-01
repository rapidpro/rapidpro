from django.utils.translation import gettext_lazy as _
from smartmin.views import SmartFormView
from django.core.exceptions import ValidationError
from ...models import Channel
from ...views import ClaimViewMixin
from django import forms
import requests

class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        bot_name = forms.CharField(required=True, help_text=_("The name of bot"))
        bot_id = forms.CharField(required=True, help_text=_("The ID of bot"))
        app_id = forms.CharField(required=True, help_text=_("The App ID"))
        app_password = forms.CharField(required=True, help_text=_("The application password"))
        tenant_id = forms.CharField(required=True, help_text=_("The Tenant ID"))

        def clean(self):
            try:

                headers = {'Content-Type': 'application/x-www-form-urlencoded'}
                request_body = {
                    "client_id": self.cleaned_data["app_id"],
                    "grant_type": "client_credentials",
                    "scope": "https://api.botframework.com/.default",
                    "client_secret": self.cleaned_data["app_password"]
                }

                resp = requests.post(
                    "https://login.microsoftonline.com/botframework.com/oauth2/v2.0/token",
                    data=request_body,
                    headers=headers
                )

                if resp.status_code != 200:  # pragma: no cover
                    raise Exception("Received non-200 response: %d", resp.status_code)

                self.cleaned_data["auth_token"] = resp.json()["access_token"]

            except Exception:
                raise forms.ValidationError(
                    _("Unable to complete login for your Microsoft Teams bot, please check information about your APP.")
                )

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        from .type import TeamsType
        
        org = self.request.user.get_org()

        auth_token = form.cleaned_data["auth_token"]
        name = form.cleaned_data["bot_name"]
        app_password = form.cleaned_data["app_password"]
        tenant_id = form.cleaned_data["tenant_id"]
        app_id = form.cleaned_data["app_id"]
        bot_id = form.cleaned_data["bot_id"]

        config = {
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            TeamsType.CONFIG_TEAMS_BOT_NAME: name,
            TeamsType.CONFIG_TEAMS_APPLICATION_PASSWORD: app_password,
            TeamsType.CONFIG_TEAMS_TENANT_ID: tenant_id,
            TeamsType.CONFIG_TEAMS_APPLICATION_ID: app_id,
            TeamsType.CONFIG_TEAMS_BOT_ID: bot_id,
        }
        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=name, address=bot_id, config=config
        )

        return super().form_valid(form)
