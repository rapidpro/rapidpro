from smartmin.views import SmartCRUDL, SmartFormView, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models.aggregates import Max
from django.http import JsonResponse
from django.http.response import HttpResponseRedirect
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.msgs.models import Msg
from temba.orgs.views import DependencyDeleteModal, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.fields import InputWidget, SelectWidget
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
    default_order = ("-last_activity_on", "-id")
    bulk_actions = ()

    def pre_process(self, request, *args, **kwargs):
        user = self.get_user()
        if user.is_beta():
            return HttpResponseRedirect(reverse("tickets.ticket_list"))
        return super().pre_process(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        user = self.get_user()
        org = user.get_org()

        context = super().get_context_data(**kwargs)
        context["folder"] = self.folder
        context["ticketers"] = org.ticketers.filter(is_active=True).order_by("created_on")
        return context


class NoteForm(forms.ModelForm):
    note = forms.CharField(
        max_length=2048,
        required=True,
        widget=InputWidget({"hide_label": True, "textarea": True}),
        help_text=_("Notes can only be seen by the support team"),
    )

    class Meta:
        model = Ticket
        fields = ("note",)


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("list", "folder", "open", "closed", "filter", "note", "assign")

    class List(OrgPermsMixin, SmartListView):
        """
        A placeholder view for the ticket handling frontend components which fetch tickets from the endpoint below
        """

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).none()

    class Folder(OrgPermsMixin, SmartListView):

        FOLDER_MINE = "mine"
        FOLDER_UNASSIGNED = "unassigned"
        FOLDER_OPEN = "open"
        FOLDER_CLOSED = "closed"

        FOLDERS = (FOLDER_MINE, FOLDER_UNASSIGNED, FOLDER_OPEN, FOLDER_CLOSED)

        permission = "tickets.ticket_list"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<folder>{'|'.join(cls.FOLDERS)})/$"

        def get_queryset(self, **kwargs):
            org = self.request.user.get_org()
            qs = super().get_queryset(**kwargs).filter(org=org).prefetch_related("contact")

            if self.kwargs["folder"] == self.FOLDER_OPEN:
                qs = qs.filter(status=Ticket.STATUS_OPEN)
            elif self.kwargs["folder"] == self.FOLDER_UNASSIGNED:
                qs = qs.filter(status=Ticket.STATUS_OPEN, assignee=None)
            elif self.kwargs["folder"] == self.FOLDER_MINE:
                qs = qs.filter(status=Ticket.STATUS_OPEN, assignee=self.request.user)
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
            def user_as_json(u):
                return {
                    "id": u.id,
                    "first_name": u.first_name,
                    "last_name": u.last_name,
                    "email": u.email,
                }

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
                        "assignee": user_as_json(t.assignee) if t.assignee else None,
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
            from .types.internal import InternalType

            links = []

            if self.has_org_perm("tickets.ticketer_delete") and self.ticketer.ticketer_type != InternalType.slug:
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

    class Note(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Creates a note for this contact
        """

        form_class = NoteForm
        fields = ("note",)
        success_url = "hide"
        slug_url_kwarg = "uuid"
        success_message = ""
        submit_button_name = _("Save")

        def form_valid(self, form):
            self.get_object().add_note(self.request.user, note=form.cleaned_data["note"])
            return self.render_modal_response(form)

    class Assign(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(NoteForm):
            assignee = forms.ChoiceField(
                required=True,
                widget=SelectWidget(
                    attrs={
                        "searchable": True,
                        "widget_only": True,
                    }
                ),
            )

            def clean_assignee(self):
                assignee = self.data["assignee"]
                return self.org.administrators.filter(pk=assignee).union(self.org.agents.filter(pk=assignee)).first()

            def __init__(self, user, ticket, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.org = user.get_org()
                choices = [
                    (user.pk, user.get_full_name())
                    for user in self.org.administrators.all().union(self.org.agents.all())
                ]

                choices.insert(0, (-1, str(_("Unassigned"))))

                self.fields["assignee"].choices = choices
                self.fields["note"].required = False

        slug_url_kwarg = "uuid"
        form_class = Form
        fields = ("assignee", "note")
        success_url = "hide"
        success_message = ""
        submit_button_name = _("Save")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            kwargs["ticket"] = self.get_object()
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            ticket = self.get_object()
            if ticket.assignee:
                initial["assignee"] = ticket.assignee.pk
            return initial

        def form_valid(self, form):
            ticket = self.get_object()
            assignee = form.cleaned_data["assignee"]
            note = form.cleaned_data["note"]

            # if our assignee is new
            if ticket.assignee != assignee:
                ticket.assign(self.request.user, assignee=assignee, note=note)

            # otherwise just add the note if we have one
            elif note:
                ticket.add_note(self.request.user, note=form.cleaned_data["note"])

            return self.render_modal_response(form)


class TicketerCRUDL(SmartCRUDL):
    model = Ticketer
    actions = ("connect", "delete")

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@tickets.ticket_filter"
        success_url = "@orgs.org_home"
        success_message = _("Your ticketing service has been deleted.")

    class Connect(OrgPermsMixin, SmartTemplateView):
        def get_gear_links(self):
            return [dict(title=_("Home"), style="button-light", href=reverse("orgs.org_home"))]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["ticketer_types"] = [tt for tt in Ticketer.get_types() if tt.is_available_to(self.get_user())]
            return context
