import requests
from smartmin.views import SmartFormView

from django import forms
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import URN
from temba.utils.fields import ExternalURLField, SelectWidget

from ...models import Channel
from ...views import ALL_COUNTRIES, ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField(help_text=_("Your enterprise WhatsApp number"))
        country = forms.ChoiceField(
            widget=SelectWidget(attrs={"searchable": True}),
            choices=ALL_COUNTRIES,
            label=_("Country"),
            help_text=_("The country this phone number is used in"),
        )
        base_url = ExternalURLField(help_text=_("The base URL for your WhatsApp enterprise installation"))
        username = forms.CharField(
            max_length=32, help_text=_("The username to access your WhatsApp enterprise account")
        )
        password = forms.CharField(
            max_length=64, help_text=_("The password to access your WhatsApp enterprise account")
        )

        facebook_template_list_domain = forms.CharField(
            label=_("Templates Domain"),
            help_text=_("Which domain to retrieve the message templates from"),
            initial="graph.facebook.com",
        )

        facebook_business_id = forms.CharField(
            max_length=128, help_text=_("The Facebook waba-id that will be used for template syncing")
        )

        facebook_access_token = forms.CharField(
            max_length=256, help_text=_("The Facebook access token that will be used for syncing")
        )

        facebook_namespace = forms.CharField(max_length=128, help_text=_("The namespace for your WhatsApp templates"))

        def clean(self):
            # first check that our phone number looks sane
            country = self.cleaned_data["country"]
            normalized = URN.normalize_number(self.cleaned_data["number"], country)
            if not URN.validate(URN.from_parts(URN.TEL_SCHEME, normalized), country):
                raise forms.ValidationError(_("Please enter a valid phone number"))
            self.cleaned_data["number"] = normalized

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

            # check we can access their facebook templates
            from .type import TEMPLATE_LIST_URL

            if self.cleaned_data["facebook_template_list_domain"] != "graph.facebook.com":
                response = requests.get(
                    TEMPLATE_LIST_URL
                    % (self.cleaned_data["facebook_template_list_domain"], self.cleaned_data["facebook_business_id"]),
                    params=dict(access_token=self.cleaned_data["facebook_access_token"]),
                )

                if response.status_code != 200:
                    raise forms.ValidationError(
                        _(
                            "Unable to access Facebook templates, please check user id and access token and make sure "
                            + "the whatsapp_business_management permission is enabled"
                        )
                    )
            return self.cleaned_data

    form_class = Form

    def form_valid(self, form):
        from .type import (
            CONFIG_FB_ACCESS_TOKEN,
            CONFIG_FB_BUSINESS_ID,
            CONFIG_FB_NAMESPACE,
            CONFIG_FB_TEMPLATE_LIST_DOMAIN,
        )

        user = self.request.user
        org = user.get_org()

        data = form.cleaned_data

        config = {
            Channel.CONFIG_BASE_URL: data["base_url"],
            Channel.CONFIG_USERNAME: data["username"],
            Channel.CONFIG_PASSWORD: data["password"],
            Channel.CONFIG_AUTH_TOKEN: data["auth_token"],
            CONFIG_FB_BUSINESS_ID: data["facebook_business_id"],
            CONFIG_FB_ACCESS_TOKEN: data["facebook_access_token"],
            CONFIG_FB_NAMESPACE: data["facebook_namespace"],
            CONFIG_FB_TEMPLATE_LIST_DOMAIN: data["facebook_template_list_domain"],
        }

        self.object = Channel.create(
            org,
            user,
            data["country"],
            self.channel_type,
            name="WhatsApp: %s" % data["number"],
            address=data["number"],
            config=config,
            tps=45,
        )

        return super().form_valid(form)
