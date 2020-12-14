from smartmin.views import SmartCRUDL, SmartDeleteView, SmartFormView, SmartListView, SmartTemplateView

from django import forms
from django.contrib import messages
from django.http import HttpResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import BulkActionMixin, ComponentFormMixin

from .models import Ticket, Ticketer


class BaseConnectView(ComponentFormMixin, OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.request = kwargs.pop("request")
            self.ticketer_type = kwargs.pop("ticketer_type")

            super().__init__(**kwargs)

    submit_button_name = _("Connect")
    permission = "tickets.ticketer_connect"
    ticketer_type = None
    form_blurb = ""

    def __init__(self, ticketer_type):
        self.ticketer_type = ticketer_type

        super().__init__()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["ticketer_type"] = self.ticketer_type
        return kwargs

    def get_template_names(self):
        return ("tickets/types/%s/connect.html" % self.ticketer_type.slug, "tickets/ticketer_connect_form.html")

    def derive_title(self):
        return _("Connect %(ticketer)s") % {"ticketer": self.ticketer_type.name}

    def get_success_url(self):
        return reverse("tickets.ticket_filter", args=[self.object.uuid])

    def get_form_blurb(self):
        return self.form_blurb

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = mark_safe(self.get_form_blurb())
        return context


class TicketListView(OrgPermsMixin, BulkActionMixin, SmartListView):
    folder = None
    fields = ("contact", "subject", "body", "opened_on")
    select_related = ("ticketer", "contact")
    default_order = ("-opened_on",)
    bulk_actions = ()

    def get_context_data(self, **kwargs):
        org = self.get_user().get_org()

        context = super().get_context_data(**kwargs)
        context["folder"] = self.folder
        context["ticketers"] = org.ticketers.filter(is_active=True).order_by("created_on")
        return context


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("open", "closed", "filter")

    class Open(TicketListView):
        title = _("Open Tickets")
        folder = "open"
        bulk_actions = ("close",)

        def get_queryset(self, **kwargs):
            org = self.get_user().get_org()
            return super().get_queryset(**kwargs).filter(org=org, status=Ticket.STATUS_OPEN)

    class Closed(TicketListView):
        title = _("Closed Tickets")
        folder = "closed"
        bulk_actions = ("reopen",)

        def get_queryset(self, **kwargs):
            org = self.get_user().get_org()
            return super().get_queryset(**kwargs).filter(org=org, status=Ticket.STATUS_CLOSED)

    class Filter(OrgObjPermsMixin, TicketListView):
        bulk_actions = ("close", "reopen")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<ticketer>[^/]+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.ticketer.name

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(ticketer=self.ticketer)

        def get_gear_links(self):
            links = []

            if self.has_org_perm("tickets.ticketer_delete"):
                links.append(
                    dict(
                        id="ticketer-delete",
                        title=_("Delete"),
                        modax=_("Delete Ticket Service"),
                        href=reverse("tickets.ticketer_delete", args=[self.ticketer.uuid]),
                    )
                )

            if self.has_org_perm("request_logs.httplog_ticketer"):
                links.append(
                    dict(title=_("HTTP Log"), href=reverse("request_logs.httplog_ticketer", args=[self.ticketer.uuid]))
                )

            return links

        def get_object_org(self):
            return self.ticketer.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer"] = self.ticketer
            context["used_by_flows"] = self.ticketer.dependent_flows.all()[:5]
            return context

        @cached_property
        def ticketer(self):
            return Ticketer.objects.get(uuid=self.kwargs["ticketer"], is_active=True)


class TicketerCRUDL(SmartCRUDL):
    model = Ticketer
    actions = ("connect", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        slug_url_kwarg = "uuid"
        cancel_url = "uuid@tickets.ticket_filter"
        title = _("Delete Ticketing Service")
        success_message = ""
        submit_button_name = _("Delete")
        fields = ("uuid",)

        def get_context_data(self, **kwargs):  # pragma: needs cover
            context = super().get_context_data(**kwargs)
            ticketer = self.get_object()
            context["used_by_flows"] = ticketer.dependent_flows.all()[:5]
            return context

        def get_success_url(self):
            return reverse("orgs.org_home")

        def post(self, request, *args, **kwargs):
            service = self.get_object()
            service.release()

            messages.info(request, _("Your ticketing service has been deleted."))
            response = HttpResponse()
            response["Temba-Success"] = self.get_success_url()
            return response

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_gear_links(self):
            return [dict(title=_("Home"), style="button-light", href=reverse("orgs.org_home"),)]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer_types"] = [tt for tt in Ticketer.get_types() if tt.is_available_to(self.get_user())]
            return context
