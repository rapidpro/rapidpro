
import requests
from smartmin.views import SmartFormView, SmartUpdateView

from django import forms
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import URN
from temba.orgs.views import OrgPermsMixin
from temba.utils.views import PostOnlyMixin

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin
from .tasks import refresh_whatsapp_contacts


class RefreshView(PostOnlyMixin, OrgPermsMixin, SmartUpdateView):
    """
    Responsible for firing off our contact refresh task
    """

    model = Channel
    fields = ()
    success_message = _("Contacts refresh begun, it may take a few minutes to complete.")
    success_url = "uuid@channels.channel_configuration"
    permission = "channels.channel_claim"
    slug_url_kwarg = "uuid"

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(org=self.get_user().get_org())

    def post_save(self, obj):
        refresh_whatsapp_contacts.delay(obj.id)
        return obj


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(help_text=_("Your enterprise WhatsApp number"))
        country = forms.ChoiceField(
            choices=ALL_COUNTRIES, label=_("Country"), help_text=_("The country this phone number is used in")
        )
        base_url = forms.URLField(help_text=_("The base URL for your WhatsApp enterprise installation"))
        username = forms.CharField(
            max_length=32, help_text=_("The username to access your WhatsApp enterprise account")
        )
        password = forms.CharField(
            max_length=64, help_text=_("The password to access your WhatsApp enterprise account")
        )

        def clean(self):
            # first check that our phone number looks sane
            number, valid = URN.normalize_number(self.cleaned_data["number"], self.cleaned_data["country"])
            if not valid:
                raise forms.ValidationError(_("Please enter a valid phone number"))
            self.cleaned_data["number"] = number

            try:
                resp = requests.post(
                    self.cleaned_data["base_url"] + "/v1/users/login",
                    auth=(self.cleaned_data["username"], self.cleaned_data["password"]),
                )

                if resp.status_code != 200:
                    raise Exception("Received non-200 response: %d", resp.status_code)

                self.cleaned_data["auth_token"] = resp.json()["users"][0]["token"]

            except Exception:
                raise forms.ValidationError(
                    _("Unable to check WhatsApp enterprise account, please check username and password")
                )

            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        user = self.request.user
        org = user.get_org()

        if not org:  # pragma: no cover
            raise Exception(_("No org for this user, cannot claim"))

        data = form.cleaned_data

        config = {
            Channel.CONFIG_BASE_URL: data["base_url"],
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
            Channel.CONFIG_AUTH_TOKEN: data["auth_token"],
        }

        self.object = Channel.create(
            org,
            user,
            data["country"],
            "WA",
            name="WhatsApp: %s" % data["number"],
            address=data["number"],
            config=config,
            tps=15,
        )

        return super().form_valid(form)
