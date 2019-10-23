from smartmin.views import SmartCRUDL, SmartListView, SmartReadView

from django.db.models import Prefetch
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.airtime.models import AirtimeTransfer
from temba.contacts.models import URN, ContactURN
from temba.orgs.views import OrgObjPermsMixin, OrgPermsMixin
from temba.request_logs.models import HTTPLog


class AirtimeCRUDL(SmartCRUDL):
    model = AirtimeTransfer
    actions = ("list", "read")

    class List(OrgPermsMixin, SmartListView):
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

        def derive_queryset(self, **kwargs):
            return AirtimeTransfer.objects.filter(org=self.derive_org())

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org"] = self.derive_org()
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
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

        def derive_queryset(self, **kwargs):
            logs_prefetch = Prefetch("http_logs", HTTPLog.objects.order_by("created_on", "id"))
            return AirtimeTransfer.objects.filter(org=self.derive_org()).prefetch_related(logs_prefetch)

        def get_context_data(self, **kwargs):
            org = self.derive_org()
            user = self.request.user

            context = super().get_context_data(**kwargs)
            context["show_logs"] = not org.is_anon or user.is_superuser or user.is_staff
            return context
