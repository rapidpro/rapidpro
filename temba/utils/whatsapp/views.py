from smartmin.views import SmartUpdateView

from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel
from temba.channels.views import ChannelTypeMixin
from temba.orgs.views.mixins import OrgPermsMixin
from temba.utils.views.mixins import PostOnlyMixin

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
