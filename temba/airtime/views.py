from smartmin.views import SmartCRUDL

from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.airtime.models import AirtimeTransfer
from temba.contacts.models import URN, ContactURN
from temba.orgs.views.base import BaseListView, BaseReadView
from temba.utils.views.mixins import SpaMixin


class AirtimeCRUDL(SmartCRUDL):
    model = AirtimeTransfer
    actions = ("list", "read")

    class List(SpaMixin, BaseListView):
        menu_path = "/settings/workspace"
        title = _("Recent Airtime Transfers")
        fields = ("status", "contact", "recipient", "currency", "actual_amount", "created_on")
        field_config = {"created_on": {"label": "Time"}, "actual_amount": {"label": "Amount"}}
        link_fields = ("status", "contact")
        default_order = ("-created_on",)

        def get_status(self, obj):
            return obj.get_status_display()

        def get_recipient(self, obj):
            org = self.derive_org()
            return ContactURN.ANON_MASK_HTML if org.is_anon else URN.format(obj.recipient, international=True)

        def lookup_field_link(self, context, field, obj):
            """
            By default we just return /view/{{ id }}/ for the current object.
            """
            if field == "contact":
                return reverse("contacts.contact_read", args=[obj.contact.uuid])

            return super().lookup_field_link(context, field, obj)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org"] = self.derive_org()
            return context

    class Read(SpaMixin, BaseReadView):
        menu_path = "/settings/workspace"
        title = _("Airtime Transfer Details")
        fields = (
            "status",
            "sender",
            "contact",
            "recipient",
            "currency",
            "desired_amount",
            "actual_amount",
            "created_on",
        )
        field_config = {"created_on": {"label": "Time"}}

        def get_status(self, obj):
            return obj.get_status_display()

        def get_sender(self, obj):
            return URN.format(obj.sender, international=True) if obj.sender else "--"

        def get_recipient(self, obj):
            org = self.derive_org()
            return ContactURN.ANON_MASK_HTML if org.is_anon else URN.format(obj.recipient, international=True)

        def get_context_data(self, **kwargs):
            org = self.derive_org()
            user = self.request.user

            context = super().get_context_data(**kwargs)

            context["show_logs"] = not org.is_anon or user.is_staff
            context["http_logs"] = self.get_object().http_logs.order_by("created_on", "id")

            return context
