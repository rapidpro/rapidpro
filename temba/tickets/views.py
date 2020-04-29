from smartmin.views import SmartCRUDL, SmartDeleteView, SmartFormView, SmartReadView, SmartTemplateView

from django import forms
from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
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
        return reverse("tickets.ticketer_read", args=[self.object.uuid])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.ticketer_type.get_form_blurb()
        return context


class TicketerCRUDL(SmartCRUDL):
    model = Ticketer
    actions = ("read", "connect", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        slug_url_kwarg = "uuid"
        cancel_url = "uuid@tickets.ticketer_read"
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

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_gear_links(self):
            links = []
            if self.has_org_perm("tickets.ticketer_delete"):
                links.append(dict(title=_("Delete"), js_class="delete-ticketer", href="#"))

            return links

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

        def get_context_data(self, **kwargs):
            tickets = (
                self.object.tickets.filter(status=Ticket.STATUS_OPEN).order_by("-opened_on").select_related("contact")
            )
            context = super().get_context_data(**kwargs)
            context["tickets"] = tickets
            return context

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer_types"] = Ticketer.get_types()
            return context
