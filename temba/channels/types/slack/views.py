import slack_sdk
from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        user_token = forms.CharField(
            label=_("User OAuth Token"),
            help_text=_(
                "In Slack select your bot app and go to Features / OAuth & Permissions to see this information."
            ),
        )
        bot_token = forms.CharField(
            label=_("Bot User OAuth Token"),
            help_text=_(
                "In Slack select your bot app and go to Features / OAuth & Permissions to see this information."
            ),
        )
        verification_token = forms.CharField(
            label=_("Verification Token"),
            help_text=_("In Slack go to Settings / Basic information, find in App Credentials and paste here."),
        )

        def clean_user_token(self):
            value = self.cleaned_data["user_token"]

            try:
                client = slack_sdk.WebClient(token=value)
                client.api_call(api_method="auth.test")
            except slack_sdk.errors.SlackApiError:
                raise ValidationError(_("Your user token is invalid, please check and try again"))

            return value

        def clean(self):
            value = self.cleaned_data.get("bot_token")

            try:
                client = slack_sdk.WebClient(token=value)
                appAuthTest = client.api_call(api_method="auth.test")
            except slack_sdk.errors.SlackApiError:
                raise ValidationError(_("Your bot user token is invalid, please check and try again"))

            self.cleaned_data["address"] = appAuthTest["bot_id"]
            return super().clean()

    def form_valid(self, form):
        from .type import SlackType

        user_token = form.cleaned_data["user_token"]
        bot_token = form.cleaned_data["bot_token"]
        verification_token = form.cleaned_data["verification_token"]

        client = slack_sdk.WebClient(token=bot_token)

        auth_test = client.api_call(
            api_method="auth.test",
        )

        config = {
            SlackType.CONFIG_BOT_TOKEN: bot_token,
            SlackType.CONFIG_USER_TOKEN: user_token,
            SlackType.CONFIG_VERIFICATION_TOKEN: verification_token,
        }

        self.object = Channel.create(
            org=self.request.org,
            user=self.request.user,
            country=None,
            channel_type=self.channel_type,
            name=auth_test["user"],
            address=auth_test["bot_id"],
            config=config,
        )

        return super().form_valid(form)

    form_class = Form
    success_url = "uuid@channels.channel_configuration"
