from random import randint

import requests
from smartmin.views import SmartFormView

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse

from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        number = forms.CharField()
        verified_name = forms.CharField()
        phone_number_id = forms.CharField()
        waba_id = forms.CharField()
        business_id = forms.CharField()
        currency = forms.CharField()
        message_template_namespace = forms.CharField()

    form_class = Form

    def pre_process(self, request, *args, **kwargs):
        oauth_user_token = self.request.session.get(Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN, None)
        if not oauth_user_token:
            self.remove_token_credentials_from_session()
            return HttpResponseRedirect(reverse("orgs.org_whatsapp_cloud_connect"))

        app_id = settings.FACEBOOK_APPLICATION_ID
        app_secret = settings.FACEBOOK_APPLICATION_SECRET

        url = "https://graph.facebook.com/v13.0/debug_token"
        params = {"access_token": f"{app_id}|{app_secret}", "input_token": oauth_user_token}

        response = requests.get(url, params=params)
        if response.status_code != 200:  # pragma: no cover
            self.remove_token_credentials_from_session()
            return HttpResponseRedirect(reverse("orgs.org_whatsapp_cloud_connect"))

        response_json = response.json()
        for perm in ["business_management", "whatsapp_business_management", "whatsapp_business_messaging"]:
            if perm not in response_json["data"]["scopes"]:
                self.remove_token_credentials_from_session()
                return HttpResponseRedirect(reverse("orgs.org_whatsapp_cloud_connect"))

        return super().pre_process(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        oauth_user_token = self.request.session.get(Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN, None)
        app_id = settings.FACEBOOK_APPLICATION_ID
        app_secret = settings.FACEBOOK_APPLICATION_SECRET

        url = "https://graph.facebook.com/v13.0/debug_token"
        params = {"access_token": f"{app_id}|{app_secret}", "input_token": oauth_user_token}

        response = requests.get(url, params=params)
        if response.status_code != 200:  # pragma: no cover
            context["waba_details"] = []

        else:
            response_json = response.json()

            waba_targets = []
            granular_scopes = response_json.get("data", dict()).get("granular_scopes", [])
            for scope_dict in granular_scopes:
                if scope_dict["scope"] in ["whatsapp_business_management", "whatsapp_business_messaging"]:
                    waba_targets.extend(scope_dict["target_ids"])

            seen_waba = []
            phone_numbers = []

            for target_waba in waba_targets:
                if target_waba in seen_waba:
                    continue

                seen_waba.append(target_waba)

                url = f"https://graph.facebook.com/v13.0/{target_waba}"
                params = {
                    "access_token": oauth_user_token,
                    "fields": "id,name,currency,message_template_namespace,owner_business_info,account_review_status,on_behalf_of_business_info,primary_funding_id,purchase_order_number,timezone_id",
                }
                response = requests.get(url, params=params)
                response_json = response.json()

                target_waba_details = response_json

                url = f"https://graph.facebook.com/v13.0/{target_waba}/phone_numbers"
                params = {"access_token": oauth_user_token}
                response = requests.get(url, params=params)
                response_json = response.json()

                target_waba_phone_numbers = response_json.get("data", [])
                for target_phone in target_waba_phone_numbers:
                    phone_numbers.append(
                        dict(
                            verified_name=target_phone["verified_name"],
                            display_phone_number=target_phone["display_phone_number"],
                            phone_number_id=target_phone["id"],
                            waba_id=target_waba_details["id"],
                            currency=target_waba_details["currency"],
                            business_id=target_waba_details["on_behalf_of_business_info"]["id"],
                            message_template_namespace=target_waba_details["message_template_namespace"],
                        )
                    )

            context["phone_numbers"] = phone_numbers

        context["claim_url"] = reverse("channels.types.whatsapp_cloud.claim")

        return context

    def form_valid(self, form):
        org = self.request.user.get_org()

        number = form.cleaned_data["number"]
        verified_name = form.cleaned_data["verified_name"]
        phone_number_id = form.cleaned_data["phone_number_id"]
        waba_id = form.cleaned_data["waba_id"]
        business_id = form.cleaned_data["business_id"]
        currency = form.cleaned_data["currency"]
        message_template_namespace = form.cleaned_data["message_template_namespace"]
        pin = str(randint(100000, 999999))

        config = {
            "wa_number": number,
            "wa_verified_name": verified_name,
            "wa_waba_id": waba_id,
            "wa_currency": currency,
            "wa_business_id": business_id,
            "wa_message_template_namespace": message_template_namespace,
            "wa_pin": pin,
        }

        self.object = Channel.create(
            org, self.request.user, None, self.channel_type, name=verified_name, address=phone_number_id, config=config
        )
        self.remove_token_credentials_from_session()
        return super().form_valid(form)

    def remove_token_credentials_from_session(self):
        if Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN in self.request.session:
            del self.request.session[Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN]
