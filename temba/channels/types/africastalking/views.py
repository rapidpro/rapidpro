from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.utils.fields import SelectWidget

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        shortcode = forms.CharField(max_length=6, min_length=1, help_text=_("Your short code on Africa's Talking"))
        country = forms.ChoiceField(
            choices=(
                ("BJ", ("Benin")),
                ("BW", ("Botswana")),
                ("CM", ("Cameroon")),
                ("CI", ("Côte d'Ivoire")),
                ("SZ", ("Eswatini")),
                ("ET", ("Ethiopia")),
                ("GH", ("Ghana")),
                ("KE", ("Kenya")),
                ("LS", ("Lesotho")),
                ("MW", ("Malawi")),
                ("ML", ("Mali")),
                ("NA", ("Namibia")),
                ("NG", ("Nigeria")),
                ("RW", ("Rwanda")),
                ("SN", ("Senegal")),
                ("ZA", ("South Africa")),
                ("TZ", ("Tanzania")),
                ("UG", ("Uganda")),
                ("ZM", ("Zambia")),
                ("ZW", ("Zimbabwe")),
                ("BF", ("Burkina Faso")),
            ),
            widget=SelectWidget(attrs={"searchable": True}),
        )
        is_shared = forms.BooleanField(
            initial=False, required=False, help_text=_("Whether this short code is shared with others")
        )
        username = forms.CharField(max_length=32, help_text=_("Your username on Africa's Talking"))
        api_key = forms.CharField(max_length=128, help_text=_("Your API key on Africa's Talking account"))

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = self.request.org
        data = form.cleaned_data
        config = dict(username=data["username"], api_key=data["api_key"], is_shared=data["is_shared"])

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name="Africa's Talking: %s" % data["shortcode"],
            address=data["shortcode"],
            config=config,
        )

        return super().form_valid(form)
