from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import ugettext_lazy as _

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        page_access_token = forms.CharField(
            min_length=32, required=True, help_text=_("The Page Access Token for your Application")
        )
        page_name = forms.CharField(required=True, help_text=_("The name of the Facebook page"))
        page_id = forms.IntegerField(required=True, help_text="The Facebook Page ID")

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        auth_token = form.cleaned_data["page_access_token"]
        name = form.cleaned_data["page_name"]
        page_id = form.cleaned_data["page_id"]

        config = {
            Channel.CONFIG_AUTH_TOKEN: auth_token,
            Channel.CONFIG_PAGE_NAME: name,
            Channel.CONFIG_SECRET: Channel.generate_secret(),
        }
        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=name, address=page_id, config=config
        )

        return super().form_valid(form)
