from smartmin.views import SmartCRUDL, SmartDeleteView, SmartFormView, SmartReadView, SmartTemplateView

from django.contrib import messages
from django.http import HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin

from .models import TicketService


class BaseConnectView(OrgPermsMixin, SmartFormView):
    permission = "tickets.ticketservice_connect"
    service_type = None

    def __init__(self, service_type):
        self.service_type = service_type
        super().__init__()

    def get_template_names(self):
        return ("tickets/types/%s/connect.html" % self.service_type.slug, "tickets/ticketservice_connect_form.html")

    def derive_title(self):
        return _("Connect") + " " + self.service_type.name

    def get_success_url(self):
        return reverse("tickets.ticketservice_read", args=[self.object.uuid])

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = self.service_type.get_form_blurb()
        return context


class TicketServiceCRUDL(SmartCRUDL):
    model = TicketService
    actions = ("read", "connect", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        slug_url_kwarg = "uuid"
        cancel_url = "uuid@tickets.ticketservice_read"
        title = _("Delete Ticket Service")
        success_message = ""
        fields = ("uuid",)

        def get_success_url(self):
            return reverse("orgs.org_home")

        def post(self, request, *args, **kwargs):
            service = self.get_object()
            service.release()

            messages.info(request, _("Your ticket service has been deleted."))
            return HttpResponseRedirect(self.get_success_url())

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        exclude = ("id", "is_active", "created_by", "modified_by", "modified_on")

        def get_gear_links(self):
            links = []
            if self.has_org_perm("tickets.ticketservice_delete"):
                links.append(dict(title=_("Delete"), js_class="delete-ticketservice", href="#"))

            return links

        def get_queryset(self, **kwargs):
            queryset = super().get_queryset(**kwargs)
            return queryset.filter(org=self.request.user.get_org(), is_active=True)

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["service_types"] = TicketService.get_types()
            return context
