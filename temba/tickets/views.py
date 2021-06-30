from smartmin.views import SmartCRUDL, SmartFormView, SmartListView, SmartReadView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models.aggregates import Max
from django.http import JsonResponse
from django.urls import reverse
from django.utils.html import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.msgs.models import Msg
from temba.orgs.views import DependencyDeleteModal, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.fields import InputWidget
from temba.utils.views import ComponentFormMixin

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
    success_url = "@tickets.ticket_list"

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

    def get_form_blurb(self):
        return self.form_blurb

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["form_blurb"] = mark_safe(self.get_form_blurb())
        return context


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("list", "folder", "note")

    class List(OrgPermsMixin, SmartListView):
        """
        A placeholder view for the ticket handling frontend components which fetch tickets from the endpoint below
        """

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).none()

    class Folder(OrgPermsMixin, SmartListView):
        FOLDER_OPEN = "open"
        FOLDER_CLOSED = "closed"
        FOLDERS = (FOLDER_OPEN, FOLDER_CLOSED)

        permission = "tickets.ticket_list"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<folder>{'|'.join(cls.FOLDERS)})/$"

        def get_queryset(self, **kwargs):
            org = self.request.user.get_org()
            qs = super().get_queryset(**kwargs).filter(org=org).prefetch_related("contact")

            if self.kwargs["folder"] == self.FOLDER_OPEN:
                qs = qs.filter(status=Ticket.STATUS_OPEN)
            else:  # self.FOLDER_CLOSED:
                qs = qs.filter(status=Ticket.STATUS_CLOSED)

            return qs.order_by("-last_activity_on", "-id")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # convert queryset to list so it can't change later
            tickets = list(context["object_list"])
            context["object_list"] = tickets

            # get the last message for each contact that these tickets belong to
            contact_ids = {t.contact_id for t in tickets}
            last_msg_ids = (
                Msg.objects.filter(contact_id__in=contact_ids).values("contact").annotate(last_msg=Max("id"))
            )
            last_msgs = Msg.objects.filter(id__in=[m["last_msg"] for m in last_msg_ids]).select_related(
                "broadcast__created_by"
            )

            context["last_msgs"] = {m.contact: m for m in last_msgs}

            return context

        def render_to_response(self, context, **response_kwargs):
            def msg_as_json(m):
                sender = None
                if m.broadcast and m.broadcast.created_by:
                    sender = {"id": m.broadcast.created_by.id, "email": m.broadcast.created_by.email}

                return {
                    "text": m.text,
                    "direction": m.direction,
                    "type": m.msg_type,
                    "created_on": m.created_on,
                    "sender": sender,
                }

            def as_json(t):
                """
                Converts a ticket to the contact-centric format expected by our frontend components
                """
                last_msg = context["last_msgs"].get(t.contact)
                return {
                    "uuid": str(t.contact.uuid),
                    "name": t.contact.get_display(),
                    "last_seen_on": t.contact.last_seen_on,
                    "last_msg": msg_as_json(last_msg) if last_msg else None,
                    "ticket": {
                        "uuid": str(t.uuid),
                        "subject": t.subject,
                        "closed_on": t.closed_on,
                    },
                }

            results = {"results": [as_json(t) for t in context["object_list"]]}

            # build up our next link if we have more
            if context["page_obj"].has_next():
                folder_url = reverse("tickets.ticket_folder", kwargs={"folder": self.kwargs["folder"]})
                next_page = context["page_obj"].number + 1
                results["next"] = f"{folder_url}?page={next_page}"

            return JsonResponse(results)

    class Note(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Creates a note for this contact
        """

        class Form(forms.Form):
            text = forms.CharField(
                max_length=2048,
                required=True,
                widget=InputWidget({"hide_label": True, "textarea": True}),
                help_text=_("Notes can only be seen by the support team"),
            )

            def __init__(self, instance, **kwargs):
                super().__init__(**kwargs)

        form_class = Form
        fields = ("text",)
        success_url = "hide"
        slug_url_kwarg = "uuid"
        success_message = ""
        submit_button_name = _("Add Note")

        def form_valid(self, form):
            self.get_object().add_note(self.request.user, note=form.cleaned_data["text"])
            return self.render_modal_response(form)


class TicketerCRUDL(SmartCRUDL):
    model = Ticketer
    actions = ("connect", "read", "delete")

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_gear_links(self):
            return [dict(title=_("Home"), style="button-light", href=reverse("orgs.org_home"))]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer_types"] = [tt for tt in Ticketer.get_types() if tt.is_available_to(self.get_user())]
            return context

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@tickets.ticket_filter"
        success_url = "@orgs.org_home"
        success_message = _("Your ticketing service has been deleted.")
