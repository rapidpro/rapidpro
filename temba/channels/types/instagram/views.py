import logging

import requests
from smartmin.views import SmartFormView, SmartModelActionView

from django import forms
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.orgs.views import ModalMixin, OrgObjPermsMixin
from temba.utils.text import truncate

from ...models import Channel
from ...views import ClaimViewMixin

logger = logging.getLogger(__name__)


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):

        user_access_token = forms.CharField(min_length=32, required=True, help_text=_("The User Access Token"))
        page_name = forms.CharField(required=True, help_text=_("The name of the Facebook page"))
        page_id = forms.IntegerField(required=True, help_text="The Facebook Page ID")

        def clean(self):
            try:
                auth_token = self.cleaned_data["user_access_token"]
                name = self.cleaned_data["page_name"]
                page_id = self.cleaned_data["page_id"]

                app_id = settings.FACEBOOK_APPLICATION_ID
                app_secret = settings.FACEBOOK_APPLICATION_SECRET

                url = "https://graph.facebook.com/v12.0/debug_token"
                params = {"access_token": f"{app_id}|{app_secret}", "input_token": auth_token}

                response = requests.get(url, params=params)
                if response.status_code != 200:  # pragma: no cover
                    raise Exception("Failed to get user ID")

                response_json = response.json()

                fb_user_id = response_json.get("data", dict()).get("user_id")
                expires_at = response_json.get("data", dict()).get("expires_at")

                if expires_at != 0:
                    # get user long lived access token
                    url = "https://graph.facebook.com/oauth/access_token"
                    params = {
                        "grant_type": "fb_exchange_token",
                        "client_id": app_id,
                        "client_secret": app_secret,
                        "fb_exchange_token": auth_token,
                    }

                    response = requests.get(url, params=params)
                    if response.status_code != 200:  # pragma: no cover
                        raise Exception("Failed to get a user long lived token")

                    long_lived_auth_token = response.json().get("access_token", "")

                    if long_lived_auth_token == "":  # pragma: no cover
                        raise Exception("Empty user access token!")

                    auth_token = long_lived_auth_token

                url = f"https://graph.facebook.com/v12.0/{fb_user_id}/accounts"
                params = {"access_token": auth_token}

                page_access_token = ""

                while True:
                    response = requests.get(url, params=params)
                    response_json = response.json()

                    if response.status_code != 200:  # pragma: no cover
                        raise Exception("Failed to get a page long lived token")

                    for page in response_json.get("data", []):
                        if page["id"] == str(page_id):
                            page_access_token = page["access_token"]
                            name = page["name"]
                            break

                    if page_access_token != "":
                        break

                    next_ = response_json["paging"].get("next", None)  # pragma: needs cover
                    if next_:  # pragma: needs cover
                        url = next_

                    else:
                        break  # pragma: needs cover

                if page_access_token == "":  # pragma: no cover
                    raise Exception("Empty page access token!")

                url = f"https://graph.facebook.com/v12.0/{page_id}/subscribed_apps"
                params = {"access_token": page_access_token}
                data = {"subscribed_fields": "messages,messaging_postbacks"}

                response = requests.post(url, data=data, params=params)

                if response.status_code != 200:  # pragma: no cover
                    raise Exception("Failed to subscribe to app for webhook events")

                self.cleaned_data["page_access_token"] = page_access_token
                self.cleaned_data["name"] = truncate(name, Channel._meta.get_field("name").max_length)

                # requires instagram_basic permission
                # https://developers.facebook.com/docs/instagram-api/reference/page#read
                url = f"https://graph.facebook.com/{page_id}?fields=instagram_business_account"
                params = {"access_token": auth_token}

                response = requests.get(url, params=params)

                if response.status_code != 200:  # pragma: no cover
                    raise Exception("Failed to get IG user")

                response_json = response.json()
                self.cleaned_data["ig_user_id"] = response_json.get("instagram_business_account").get("id")

            except Exception as e:
                logger.error(f"Unable to connect Instagram channel with error: {str(e)}", exc_info=True)
                raise forms.ValidationError(_("Sorry your Instagram channel could not be connected. Please try again"))

            return self.cleaned_data

    form_class = Form

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["claim_url"] = reverse("channels.types.instagram.claim")
        context["facebook_app_id"] = settings.FACEBOOK_APPLICATION_ID

        claim_error = None
        if context["form"].errors:
            claim_error = context["form"].errors["__all__"][0]
        context["claim_error"] = claim_error
        return context

    def form_valid(self, form):
        page_id = form.cleaned_data["page_id"]
        page_access_token = form.cleaned_data["page_access_token"]
        name = form.cleaned_data["name"]
        ig_user_id = form.cleaned_data["ig_user_id"]

        config = {
            Channel.CONFIG_AUTH_TOKEN: page_access_token,
            Channel.CONFIG_PAGE_NAME: name,
            "page_id": page_id,
        }

        self.object = Channel.create(
            self.request.org,
            self.request.user,
            None,
            self.channel_type,
            name=name,
            address=ig_user_id,
            config=config,
        )

        return super().form_valid(form)


class RefreshToken(ModalMixin, OrgObjPermsMixin, SmartModelActionView):
    class Form(forms.Form):
        user_access_token = forms.CharField(min_length=32, required=True, help_text=_("The User Access Token"))
        fb_user_id = forms.CharField(
            required=True,
            help_text=_("The Facebook User ID of the admin that connected the channel"),
        )

    slug_url_kwarg = "uuid"
    success_url = "uuid@channels.channel_read"
    form_class = Form
    permission = "channels.channel_claim"
    fields = ()
    template_name = "channels/types/instagram/refresh_token.html"
    title = _("Reconnect Instagram Business Account")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["refresh_url"] = reverse("channels.types.instagram.refresh_token", args=(self.object.uuid,))

        app_id = settings.FACEBOOK_APPLICATION_ID
        app_secret = settings.FACEBOOK_APPLICATION_SECRET

        context["facebook_app_id"] = app_id

        url = "https://graph.facebook.com/v12.0/debug_token"
        params = {
            "access_token": f"{app_id}|{app_secret}",
            "input_token": self.object.config[Channel.CONFIG_AUTH_TOKEN],
        }
        resp = requests.get(url, params=params)

        error_connect = False
        if resp.status_code != 200:
            error_connect = True
        else:
            valid_token = resp.json().get("data", dict()).get("is_valid", False)
            if not valid_token:
                error_connect = True

        context["error_connect"] = error_connect

        return context

    def get_queryset(self):
        return self.request.org.channels.filter(is_active=True, channel_type="IG")

    def execute_action(self):

        form = self.form
        channel = self.object

        auth_token = form.data["user_access_token"]
        fb_user_id = form.data["fb_user_id"]

        page_id = channel.config.get("page_id")

        if page_id is None:
            raise Exception("Failed to get channel page ID")  # pragma: needs cover

        app_id = settings.FACEBOOK_APPLICATION_ID
        app_secret = settings.FACEBOOK_APPLICATION_SECRET

        # get user long lived access token
        url = "https://graph.facebook.com/oauth/access_token"
        params = {
            "grant_type": "fb_exchange_token",
            "client_id": app_id,
            "client_secret": app_secret,
            "fb_exchange_token": auth_token,
        }

        response = requests.get(url, params=params)

        if response.status_code != 200:  # pragma: no cover
            raise Exception("Failed to get a user long lived token")

        long_lived_auth_token = response.json().get("access_token", "")

        if long_lived_auth_token == "":  # pragma: no cover
            raise Exception("Empty user access token!")

        url = f"https://graph.facebook.com/v12.0/{fb_user_id}/accounts"
        params = {"access_token": long_lived_auth_token}

        page_access_token = ""

        while True:
            response = requests.get(url, params=params)
            response_json = response.json()

            if response.status_code != 200:  # pragma: no cover
                raise Exception("Failed to get a page long lived token")

            for page in response_json.get("data", []):
                if page["id"] == str(page_id):
                    page_access_token = page["access_token"]
                    name = page["name"]
                    break

            if page_access_token != "":
                break

            next_ = response_json["paging"].get("next", None)  # pragma: needs cover
            if next_:  # pragma: needs cover
                url = next_

            else:  # pragma: needs cover
                break

        url = f"https://graph.facebook.com/v12.0/{page_id}/subscribed_apps"
        params = {"access_token": page_access_token}
        data = {"subscribed_fields": "messages,messaging_postbacks"}

        response = requests.post(url, data=data, params=params)

        if response.status_code != 200:  # pragma: no cover
            raise Exception("Failed to subscribe to app for webhook events")

        channel.config[Channel.CONFIG_AUTH_TOKEN] = page_access_token
        channel.config[Channel.CONFIG_PAGE_NAME] = name
        channel.save(update_fields=["config"])
