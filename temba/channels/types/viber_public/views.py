import requests
from smartmin.views import SmartFormView

from django import forms
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin, UpdateChannelForm

CONFIG_WELCOME_MESSAGE = "welcome_message"


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        auth_token = forms.CharField(help_text=_("The authentication token provided by Viber"))

        def clean_auth_token(self):
            auth_token = self.data["auth_token"]
            response = requests.post("https://chatapi.viber.com/pa/get_account_info", json={"auth_token": auth_token})
            if response.status_code != 200 or response.json()["status"] != 0:
                raise ValidationError("Error validating authentication token: %s" % response.json()["status_message"])
            return auth_token

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        auth_token = form.cleaned_data["auth_token"]

        response = requests.post("https://chatapi.viber.com/pa/get_account_info", json={"auth_token": auth_token})
        response_json = response.json()

        name = response_json["uri"]
        address = response_json["id"]
        config = {Channel.CONFIG_AUTH_TOKEN: auth_token, Channel.CONFIG_CALLBACK_DOMAIN: org.get_brand_domain()}

        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=name, address=address, config=config
        )

        return super().form_valid(form)


class UpdateForm(UpdateChannelForm):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.add_config_field(
            CONFIG_WELCOME_MESSAGE,
            forms.CharField(
                max_length=640,
                label=_("Welcome Message"),
                required=False,
                widget=forms.Textarea,
                help_text=_(
                    "The message send to user who have not yet subscribed to the channel, changes may take up to 30 "
                    "seconds to take effect"
                ),
            ),
            "",
        )

    class Meta(UpdateChannelForm.Meta):
        fields = "name", "address", "alert_email"
        readonly = ("address",)
