from smartmin.views import SmartFormView

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.utils.translation import ugettext_lazy as _

from temba.utils.twitter import TembaTwython, TwythonError
from temba.utils.views import NonAtomicMixin

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(NonAtomicMixin, ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        api_key = forms.CharField(label=_("Consumer API Key"))
        api_secret = forms.CharField(label=_("Consumer API Secret Key"))
        access_token = forms.CharField(label=_("Access Token"))
        access_token_secret = forms.CharField(label=_("Access Token Secret"))
        env_name = forms.CharField(label=_("Environment Name"))

        def clean(self):
            cleaned_data = super().clean()
            api_key = cleaned_data.get("api_key")
            api_secret = cleaned_data.get("api_secret")
            access_token = cleaned_data.get("access_token")
            access_token_secret = cleaned_data.get("access_token_secret")

            if api_key and api_secret and access_token and access_token_secret:
                twitter = TembaTwython(api_key, api_secret, access_token, access_token_secret)
                try:
                    twitter.verify_credentials()
                except TwythonError:
                    raise ValidationError(_("The provided Twitter credentials do not appear to be valid."))

            return cleaned_data

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()

        cleaned_data = form.cleaned_data
        api_key = cleaned_data["api_key"]
        api_secret = cleaned_data["api_secret"]
        access_token = cleaned_data["access_token"]
        access_token_secret = cleaned_data["access_token_secret"]
        env_name = cleaned_data["env_name"]

        twitter = TembaTwython(api_key, api_secret, access_token, access_token_secret)
        account_info = twitter.verify_credentials()
        handle_id = str(account_info["id"])
        screen_name = account_info["screen_name"]

        config = {
            "handle_id": handle_id,
            "api_key": api_key,
            "api_secret": api_secret,
            "access_token": access_token,
            "access_token_secret": access_token_secret,
            "env_name": env_name,
            Channel.CONFIG_CALLBACK_DOMAIN: settings.HOSTNAME,
        }

        try:
            self.object = Channel.create(
                org,
                self.request.user,
                None,
                self.channel_type,
                name="@%s" % screen_name,
                address=screen_name,
                config=config,
            )
        except ValidationError as e:
            self.form.add_error(None, e)
            return self.form_invalid(form)

        return super().form_valid(form)
