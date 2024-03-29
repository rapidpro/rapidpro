import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        shortcode = forms.IntegerField(help_text=_("Your short code on DMark Mobile"))
        country = forms.ChoiceField(choices=(("UG", _("Uganda")), ("CD", _("DRC"))))
        username = forms.CharField(max_length=32, help_text=_("Your username on DMark Mobile"))
        password = forms.CharField(max_length=64, help_text=_("Your password on DMark Mobile"))

        def clean(self):
            try:
                resp = requests.post(
                    "http://smsapi1.dmarkmobile.com/get-token/",
                    data=dict(username=self.cleaned_data["username"], password=self.cleaned_data["password"]),
                )

                if resp.status_code == 200:
                    self.cleaned_data["token"] = resp.json()["token"]
                else:
                    raise Exception("Received non-200 response: %d", resp.status_code)

            except Exception:
                raise forms.ValidationError("Unable to retrieve token, please check username and password")

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        data = form.cleaned_data
        config = {
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
            Channel.CONFIG_AUTH_TOKEN: data["token"],
        }

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            data["country"],
            self.channel_type,
            name="DMark Mobile: %s" % data["shortcode"],
            address=data["shortcode"],
            config=config,
        )

        return super().form_valid(form)
