import regex

from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.http import JsonResponse
from django.utils.translation import ugettext_lazy as _
from django.urls import reverse
from smartmin.views import SmartFormView, SmartReadView
from ...models import Channel
from ...views import ClaimViewMixin


class ClaimView(ClaimViewMixin, SmartFormView):
    class Form(ClaimViewMixin.Form):
        channel_name = forms.CharField(label=_("WebChat Name"), max_length=64)

        def clean_channel_name(self):
            org = self.request.user.get_org()
            value = self.cleaned_data["channel_name"]

            if not regex.match(r"^[A-Za-z0-9_.\-*() ]+$", value, regex.V0):
                raise forms.ValidationError(
                    "Please make sure the WebChat name only contains "
                    "alphanumeric characters [0-9a-zA-Z], hyphens, and underscores"
                )

            # does a ws channel already exists on this account with that name
            existing = Channel.objects.filter(
                org=org, is_active=True, channel_type=self.channel_type.code, name=value
            ).first()

            if existing:
                raise ValidationError(_("A WebChat channel for this name already exists on your account."))

            return value

    form_class = Form

    def form_valid(self, form):
        org = self.request.user.get_org()
        cleaned_data = form.cleaned_data
        branding = org.get_branding()

        channel_name = cleaned_data.get("channel_name")
        default_theme = settings.WIDGET_THEMES.get(settings.WIDGET_DEFAULT_THEME, {})

        basic_config = {
            "title": f"Chat with {channel_name}",
            "welcome_message_default": "",
            "theme": settings.WIDGET_DEFAULT_THEME,
            "logo": f"https://{settings.HOSTNAME}{settings.STATIC_URL}{branding.get('favico')}",
            "chat_header_bg_color": default_theme.get("header_bg"),
            "chat_header_text_color": default_theme.get("header_txt"),
            "automated_chat_bg": default_theme.get("automated_chat_bg"),
            "automated_chat_txt": default_theme.get("automated_chat_txt"),
            "user_chat_bg": default_theme.get("user_chat_bg"),
            "user_chat_txt": default_theme.get("user_chat_txt"),
            "chat_timeout": 120,
        }
        languages = org.languages.all().order_by("orgs")
        for lang in languages:
            basic_config[f"welcome_message_{lang.iso_code}"] = ""

        self.object = Channel.create(
            org,
            self.request.user,
            None,
            self.channel_type,
            name=channel_name,
            config=basic_config,
            address=settings.WEBSOCKET_SERVER_URL,
        )

        return super().form_valid(form)


class ConfigurationView(SmartReadView):
    slug_url_kwarg = "uuid"

    def get_object(self, queryset=None):
        return Channel.objects.filter(uuid=self.kwargs.get("uuid"), is_active=True, channel_type="WCH").first()

    def get(self, request, *args, **kwargs):
        channel = self.get_object()
        if not channel:
            return JsonResponse(dict(error=_("Channel not found")), status=404)

        if not channel.config:
            response = dict(info=_("No configuration on this channel"))
        else:
            welcome_message = {}

            languages = channel.org.languages.all().order_by("orgs")
            for lang in languages:
                welcome_message[f"{lang.iso_code}"] = channel.config.get(f"welcome_message_{lang.iso_code}")

            if not languages:
                welcome_message["default"] = channel.config.get("welcome_message_default")

            response = {
                "socketUrl": settings.WEBSOCKET_SERVER_URL,
                "channelUUID": channel.uuid,
                "title": channel.config.get("title"),
                "autoOpen": False,
                "hostApi": f"https://{channel.callback_domain}",
                "icon": channel.config.get("logo"),
                "welcomeMessage": welcome_message,
                "theme": {
                    "widgetBackgroundColor": f"#{channel.config.get('widget_bg_color')}",
                    "chatHeaderBackgroundColor": f"#{channel.config.get('chat_header_bg_color')}",
                    "chatHeaderTextColor": f"#{channel.config.get('chat_header_text_color')}",
                    "automatedChatBackgroundColor": f"#{channel.config.get('automated_chat_bg')}",
                    "automatedChatTextColor": f"#{channel.config.get('automated_chat_txt')}",
                    "userChatBackgroundColor": f"#{channel.config.get('user_chat_bg')}",
                    "userChatTextColor": f"#{channel.config.get('user_chat_txt')}",
                },
            }
        return JsonResponse(response)
