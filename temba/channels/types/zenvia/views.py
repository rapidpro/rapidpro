from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class ZVClaimForm(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=6, min_length=1, help_text=_("The Zenvia short code"))
        username = forms.CharField(max_length=32, help_text=_("The account username provided by Zenvia"))
        password = forms.CharField(max_length=64, help_text=_("The account password provided by Zenvia"))

    form_class = ZVClaimForm

    def form_valid(self, form):
        data = form.cleaned_data
        config = {Channel.CONFIG_USERNAME: data["username"], Channel.CONFIG_PASSWORD: data["password"]}

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            "BR",
            self.channel_type,
            name="Zenvia: %s" % data["shortcode"],
            address=data["shortcode"],
            config=config,
        )

        return super().form_valid(form)
