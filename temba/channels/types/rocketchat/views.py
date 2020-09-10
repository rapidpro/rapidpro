from django import forms
from smartmin.views import SmartFormView

from temba.utils.text import random_string
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        base_url = forms.URLField()
        bot_username = forms.CharField()

        def form_valid(self):
            pass
            # criar Channel UUID (temba.utils.uuid4)
            # montar callback_url: "https://" + channel.callback_domain + reverse("courier.rc", args=[channel.uuid])
            # fazer request put na <base_url>/settings, passando bot_username e callback_url

    form_class = Form