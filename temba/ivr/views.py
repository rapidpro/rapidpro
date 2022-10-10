from smartmin.views import SmartCRUDL, SmartListView

from temba.orgs.views import OrgFilterMixin, OrgPermsMixin

from .models import Call


class CallCRUDL(SmartCRUDL):
    model = Call
    actions = ("list",)

    class List(OrgFilterMixin, OrgPermsMixin, SmartListView):
        default_order = ("-created_on",)
        select_related = ("contact", "channel")
