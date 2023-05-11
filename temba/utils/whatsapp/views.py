from smartmin.views import SmartReadView, SmartUpdateView

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ChannelTypeMixin
from temba.orgs.views import OrgPermsMixin
from temba.request_logs.models import HTTPLog
from temba.templates.models import TemplateTranslation
from temba.utils.views import ContentMenuMixin, PostOnlyMixin

from .tasks import refresh_whatsapp_contacts


class RefreshView(ChannelTypeMixin, PostOnlyMixin, OrgPermsMixin, SmartUpdateView):
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
        return super().get_queryset().filter(org=self.request.org)

    def post_save(self, obj):
        refresh_whatsapp_contacts.delay(obj.id)
        return obj


class TemplatesView(ChannelTypeMixin, ContentMenuMixin, OrgPermsMixin, SmartReadView):
    """
    Displays a simple table of all the templates synced on this whatsapp channel
    """

    model = Channel
    fields = ()
    permission = "channels.channel_read"
    slug_url_kwarg = "uuid"
    template_name = "utils/whatsapp/templates.html"

    def build_content_menu(self, menu):
        obj = self.get_object()

        menu.add_link(_("Sync Logs"), reverse(f"channels.types.{obj.type.slug}.sync_logs", args=[obj.uuid]))

    def get_queryset(self):
        return super().get_queryset().filter(org=self.request.org)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # include all our templates as well
        context["translations"] = TemplateTranslation.objects.filter(channel=self.object, is_active=True).order_by(
            "template__name"
        )
        return context

    def derive_menu_path(self):
        return f"/settings/channels/{self.get_object().uuid}"


class SyncLogsView(ChannelTypeMixin, ContentMenuMixin, OrgPermsMixin, SmartReadView):
    """
    Displays a simple table of the WhatsApp Templates Synced requests for this channel
    """

    model = Channel
    fields = ()
    permission = "channels.channel_read"
    slug_url_kwarg = "uuid"
    template_name = "utils/whatsapp/sync_logs.html"

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
