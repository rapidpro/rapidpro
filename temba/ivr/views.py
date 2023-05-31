from smartmin.views import SmartCRUDL

from django.utils.translation import gettext_lazy as _

from temba.msgs.models import SystemLabel
from temba.msgs.views import SystemLabelView

from .models import Call


class CallCRUDL(SmartCRUDL):
    model = Call
    actions = ("list",)

    class List(SystemLabelView):
        title = _("Calls")
        default_order = ("-created_on",)
        select_related = ("contact", "channel")
        system_label = SystemLabel.TYPE_CALLS
        menu_path = "/msg/calls"
