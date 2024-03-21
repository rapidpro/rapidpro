from smartmin.views import SmartCRUDL, SmartListView, SmartReadView

from django.http import Http404
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ChannelTypeMixin
from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin
from temba.request_logs.models import HTTPLog
from temba.utils.views import ContentMenuMixin, SpaMixin

from .models import TemplateTranslation


class TemplateTranslationCRUDL(SmartCRUDL):
    model = TemplateTranslation
    actions = ("channel",)

    class Channel(SpaMixin, ContentMenuMixin, OrgObjPermsMixin, SmartListView):
        permission = "channels.channel_read"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<channel>[^/]+)/$" % (path, action)

        def build_content_menu(self, menu):
            menu.add_link(
                _("Sync Logs"), reverse(f"channels.types.{self.channel.type.slug}.sync_logs", args=[self.channel.uuid])
            )

        def derive_menu_path(self):
            return f"/settings/channels/{self.channel.uuid}"

        def get_object_org(self):
            return self.channel.org

        @cached_property
        def channel(self):
            try:
                return Channel.objects.get(is_active=True, uuid=self.kwargs["channel"])
            except Channel.DoesNotExist:
                raise Http404("Channel not found")

        def derive_queryset(self, **kwargs):
            return (
                super()
                .derive_queryset(**kwargs)
                .filter(channel=self.channel, is_active=True)
                .order_by("template__name")
            )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["channel"] = self.channel
            return context


class SyncLogsView(ChannelTypeMixin, ContentMenuMixin, OrgPermsMixin, SmartReadView):
    """
    Displays a simple table of the WhatsApp Templates Synced requests for this channel
    """

    model = Channel
    fields = ()
    permission = "channels.channel_read"
    slug_url_kwarg = "uuid"
    template_name = "templates/sync_logs.html"

    def build_content_menu(self, menu):
        obj = self.get_object()

        menu.add_link(_("Message Templates"), reverse(f"channels.types.{obj.type.slug}.templates", args=[obj.uuid]))

    def get_queryset(self):
        queryset = super().get_queryset()
        return queryset.filter(org=self.request.org)

    def derive_menu_path(self):
        return f"/settings/channels/{self.get_object().uuid}"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # include all our http sync logs as well
        context["sync_logs"] = (
            HTTPLog.objects.filter(
                log_type__in=[
                    HTTPLog.WHATSAPP_TEMPLATES_SYNCED,
                    HTTPLog.WHATSAPP_TOKENS_SYNCED,
                    HTTPLog.WHATSAPP_CONTACTS_REFRESHED,
                    HTTPLog.WHATSAPP_CHECK_HEALTH,
                ],
                channel=self.object,
            )
            .order_by("-created_on")
            .prefetch_related("channel")
        )
        return context
