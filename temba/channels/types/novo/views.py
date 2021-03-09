from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class NVClaimForm(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=15, min_length=1, help_text=_("The Novo short code"))
        merchant_id = forms.CharField(
            max_length=30,
            min_length=1,
            label=_("Merchant ID"),
            help_text=_("The merchant id to compose your Merchant URL provided by Novo"),
        )
        merchant_secret = forms.CharField(
            max_length=30,
            min_length=1,
            label=_("Merchant Secret"),
            help_text=_("The merchant secret provided by Novo"),
        )

    form_class = NVClaimForm

    def form_valid(self, form):
        user = self.request.user
        data = form.cleaned_data
        org = user.get_org()

        from .type import NovoType

        config = {
            NovoType.CONFIG_MERCHANT_ID: data["merchant_id"],
            NovoType.CONFIG_MERCHANT_SECRET: data["merchant_secret"],
            Channel.CONFIG_SECRET: Channel.generate_secret(32),
        }

        self.object = Channel.create(
            org,
            user,
            "TT",
            self.channel_type,
            name="Novo: %s" % data["shortcode"],
            address=data["shortcode"],
            config=config,
        )

        return super(ClaimView, self).form_valid(form)
