from smartmin.views import (
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.views import PostOnlyMixin

from .models import Ticket, Ticketer


class BaseConnectView(OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.request = kwargs.pop("request")
            self.ticketer_type = kwargs.pop("ticketer_type")

            super().__init__(**kwargs)

    submit_button_name = _("Connect")
    permission = "tickets.ticketer_connect"
    ticketer_type = None

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
        return _("Connect") + " " + self.ticketer_type.name

    def get_success_url(self):
        return reverse("tickets.ticket_filter", args=[self.object.uuid])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.ticketer_type.get_form_blurb()
        return context


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("filter", "close")

    class Filter(OrgObjPermsMixin, SmartListView):
        fields = ("subject", "contact", "body", "opened_on")
        select_related = ("ticketer", "contact")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<ticketer>[^/]+)/$" % (path, action)

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(ticketer=self.ticketer, status=Ticket.STATUS_OPEN)
            return qs

        def get_gear_links(self):
            links = []
            if self.has_org_perm("tickets.ticketer_delete"):
                links.append(dict(title=_("Delete"), js_class="delete-ticketer", href="#"))
            if self.get_user().is_support():
                links.append(
                    dict(
                        title=_("HTTP Log"),
                        href=reverse("request_logs.httplog_list", args=["ticketer", self.ticketer.uuid]),
                    )
                )
            return links

        def get_object_org(self):
            return self.ticketer.org

        def get_context_data(self, **kwargs):
            org = self.request.user.get_org()

            context = super().get_context_data(**kwargs)
            context["ticketers"] = org.ticketers.filter(is_active=True).order_by("created_on")
            context["ticketer"] = self.ticketer
            context["used_by_flows"] = self.ticketer.dependent_flows.all()[:5]
            return context

        @cached_property
        def ticketer(self):
            return Ticketer.objects.get(uuid=self.kwargs["ticketer"], is_active=True)

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("subject", "body", "status")

        def get_gear_links(self):
            links = []
            if self.object.status == Ticket.STATUS_OPEN and self.has_org_perm("tickets.ticket_close"):
                links.append(dict(title=_("Close"), js_class="close-ticket", href="#"))
            return links

    class Close(PostOnlyMixin, OrgObjPermsMixin, SmartUpdateView):
        slug_url_kwarg = "uuid"
        fields = ()

        def save(self, obj):
            # TODO ticket should be closed on the external system too.. maybe better to let mailroom do this via a task

            obj.status = Ticket.STATUS_CLOSED
            obj.closed_on = timezone.now()
            obj.save(update_fields=("status", "closed_on"))

        def get_success_url(self):
            return reverse("tickets.ticket_filter", args=[self.object.ticketer.uuid])


class TicketerCRUDL(SmartCRUDL):
    model = Ticketer
    actions = ("connect", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        slug_url_kwarg = "uuid"
        cancel_url = "uuid@tickets.ticket_filter"
        title = _("Delete Ticketing Service")
        success_message = ""
        fields = ("uuid",)

        def get_success_url(self):
            return reverse("orgs.org_home")

        def post(self, request, *args, **kwargs):
            service = self.get_object()
            service.release()

            messages.info(request, _("Your ticketing service has been deleted."))
            return HttpResponseRedirect(self.get_success_url())

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer_types"] = [tt for tt in Ticketer.get_types() if tt.is_available()]
            return context
