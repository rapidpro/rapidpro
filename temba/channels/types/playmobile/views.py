from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.utils.fields import ExternalURLField

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class PMClaimForm(ClaimViewMixin.Form):
        base_url = ExternalURLField(label=_("Base URL"), help_text=_("The base URL for PlayMobile"))
        shortcode = forms.CharField(
            label=_("Shortcode"), max_length=15, min_length=1, help_text=_("The short code you are connecting")
        )
        username = forms.CharField(label=_("Username"), help_text=_("The username for your API account"))
        password = forms.CharField(label=_("Password"), help_text=_("The password for your API account"))

    form_class = PMClaimForm

    def form_valid(self, form):
        user = self.request.user
        data = form.cleaned_data
        org = user.get_org()

        config = {
            Channel.CONFIG_BASE_URL: data["base_url"],
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
        }

        self.object = Channel.create(
            org, user, "UZ", self.channel_type, name=data["shortcode"], address=data["shortcode"], config=config
        )

        return super(ClaimView, self).form_valid(form)
