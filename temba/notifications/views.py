from smartmin.views import SmartCRUDL, SmartListView

from django.utils.translation import gettext_lazy as _

from temba.orgs.views import OrgPermsMixin
from temba.utils.views import SpaMixin

from .mixins import NotificationTargetMixin
from .models import Incident


class IncidentCRUDL(SmartCRUDL):
    model = Incident
    actions = ("list",)

    class List(OrgPermsMixin, SpaMixin, NotificationTargetMixin, SmartListView):
        default_order = "-started_on"
        title = _("Incidents")
        menu_path = "/settings/incidents"
        notification_type = "incident:started"
        notification_scope = None  # clear all incident started notifications

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(org=self.request.org).exclude(ended_on=None)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ongoing"] = (
                Incident.objects.filter(org=self.request.org, ended_on=None)
                .select_related("org", "channel")
                .order_by("-started_on")
            )
            return context
