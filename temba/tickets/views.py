from smartmin.views import (
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin

from .models import Ticket, Ticketer


class BaseConnectView(OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        def __init__(self, **kwargs):
            self.request = kwargs.pop("request")
            self.ticketer_type = kwargs.pop("ticketer_type")

            super().__init__(**kwargs)

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
        fields = ("contact", "subject", "body", "opened_on")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<ticketer>[^/]+)/$" % (path, action)

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(ticketer=self.ticketer)  # , status=Ticket.STATUS_OPEN
            return qs

        def get_gear_links(self):
            links = []
            if self.has_org_perm("tickets.ticketer_delete"):
                links.append(dict(title=_("Delete"), js_class="delete-ticketer", href="#"))
            return links

        def get_object_org(self):
            return self.ticketer.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer"] = self.ticketer
            context["used_by_flows"] = self.ticketer.dependent_flows.count()
            return context

        @cached_property
        def ticketer(self):
            return Ticketer.objects.get(uuid=self.kwargs["ticketer"], is_active=True)

    class Close(OrgObjPermsMixin, SmartUpdateView):
        pass


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
