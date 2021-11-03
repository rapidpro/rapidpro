from smartmin.views import SmartCRUDL, SmartFormView, SmartListView, SmartReadView, SmartTemplateView, SmartUpdateView

from django import forms
from django.contrib.auth.models import User
from django.db.models.aggregates import Max
from django.http import JsonResponse
from django.urls import reverse
from django.utils.functional import cached_property
from django.utils.html import mark_safe
from django.utils.translation import ugettext_lazy as _

from temba.msgs.models import Msg
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.views import DependencyDeleteModal, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.utils.fields import InputWidget, SelectWidget
from temba.utils.views import ComponentFormMixin, SpaMixin

from .models import AllFolder, MineFolder, Ticket, TicketCount, Ticketer, TicketFolder, UnassignedFolder


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
    actions = ("list", "folder", "note", "assign", "menu")

    class List(SpaMixin, OrgPermsMixin, NotificationTargetMixin, SmartListView):
        """
        A placeholder view for the ticket handling frontend components which fetch tickets from the endpoint below
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^ticket/((?P<folder>{folders})/((?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+)/)?)?)?$"

        def get_notification_scope(self) -> tuple:
            folder, status, _, _ = self.tickets_path
            if folder == UnassignedFolder.slug and status == "open":
                return "tickets:opened", ""
            elif folder == MineFolder.slug and status == "open":
                return "tickets:activity", ""
            return "", ""

        @cached_property
        def tickets_path(self) -> tuple:
            """
            Returns tuple of folder, status, ticket uuid, and whether that ticket exists in first page of tickets
            """
            folder = self.kwargs.get("folder")
            status = self.kwargs.get("status")
            uuid = self.kwargs.get("uuid")
            in_page = False

            path = self.spa_referrer_path
            if path and len(path) > 1 and path[0] == "tickets":
                if not folder and len(path) > 1:
                    folder = path[1]
                if not status and len(path) > 2:
                    status = path[2]
                if not uuid and len(path) > 3:
                    uuid = path[3]

            # if we have a uuid make sure it is in our first page of tickets
            if uuid:
                status_code = Ticket.STATUS_OPEN if status == "open" else Ticket.STATUS_CLOSED
                org = self.request.org
                user = self.request.user
                tickets = list(
                    TicketFolder.from_slug(folder).get_queryset(org, user, True).filter(status=status_code)[:25]
                )

                found = list(filter(lambda t: str(t.uuid) == uuid, tickets))
                if found:
                    in_page = True
                else:
                    # if it's not, switch our folder to everything with that ticket's state
                    ticket = org.tickets.filter(uuid=uuid).first()
                    if ticket:
                        folder = AllFolder.slug
                        status = "open" if ticket.status == Ticket.STATUS_OPEN else "closed"

            return folder or MineFolder.slug, status or "open", uuid, in_page

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            folder, status, uuid, in_page = self.tickets_path
            context["folder"] = folder
            context["status"] = status
            context["has_tickets"] = self.request.org.tickets.exists()
            if uuid:
                context["nextUUID" if in_page else "uuid"] = uuid

            return context

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).none()

    class Menu(OrgPermsMixin, SmartTemplateView):
        def render_to_response(self, context, **response_kwargs):
            user = self.request.user
            count_by_assignee = TicketCount.get_by_assignees(user.get_org(), [None, user], Ticket.STATUS_OPEN)
            counts = {
                MineFolder.slug: count_by_assignee[user],
                UnassignedFolder.slug: count_by_assignee[None],
                AllFolder.slug: TicketCount.get_all(user.get_org(), Ticket.STATUS_OPEN),
            }

            menu = []
            for folder in TicketFolder.all().values():
                menu.append(
                    {
                        "id": folder.slug,
                        "name": folder.name,
                        "icon": folder.icon,
                        "count": counts[folder.slug],
                    }
                )
            return JsonResponse({"results": menu})

    class Folder(OrgPermsMixin, SmartTemplateView):
        permission = "tickets.ticket_list"
        paginate_by = 25

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^{path}/{action}/(?P<folder>{folders})/(?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+))?$"

        @cached_property
        def folder(self):
            return TicketFolder.from_slug(self.kwargs["folder"])

        def get_queryset(self, **kwargs):

            user = self.request.user
            status = Ticket.STATUS_OPEN if self.kwargs["status"] == "open" else Ticket.STATUS_CLOSED
            uuid = self.kwargs.get("uuid", None)
            after = int(self.request.GET.get("after", 0))
            before = int(self.request.GET.get("before", 0))

            # fetching new activity gets a different order later
            ordered = False if after else True
            qs = self.folder.get_queryset(user.get_org(), user, ordered).filter(status=status)

            # all new activity
            after = int(self.request.GET.get("after", 0))
            if after:
                after = timestamp_to_datetime(after)
                qs = qs.filter(last_activity_on__gt=after).order_by("last_activity_on", "id")

            # historical page
            if before:
                before = timestamp_to_datetime(before)
                qs = qs.filter(last_activity_on__lt=before)

            # if we have exactly one historical page, redo our query for anything including the date
            # of our last ticket to make sure we don't lose items in our paging
            if not after and not uuid:
                qs = qs[: self.paginate_by]
                count = len(qs)

                if count == self.paginate_by:
                    last_ticket = qs[len(qs) - 1]
                    qs = self.folder.get_queryset(user.get_org(), user, ordered).filter(
                        status=status, last_activity_on__gte=last_ticket.last_activity_on
                    )

                    # now reapply our before if we have one
                    if before:
                        qs = qs.filter(last_activity_on__lt=before)  # pragma: needs cover

            if uuid:
                qs = qs.filter(uuid=uuid)

            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # convert queryset to list so it can't change later
            tickets = self.get_queryset()
            context["tickets"] = tickets

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
            def topic_as_json(t):
                return {"uuid": str(t.uuid), "name": t.name}

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
                    "attachments": m.attachments,
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
                        "topic": topic_as_json(t.topic) if t.topic else None,
                        "body": t.body,
                        "last_activity_on": t.last_activity_on,
                        "closed_on": t.closed_on,
                    },
                }

            results = {"results": [as_json(t) for t in context["tickets"]]}

            # build up our next link if we have more
            if len(context["tickets"]) >= self.paginate_by:
                folder_url = reverse(
                    "tickets.ticket_folder", kwargs={"folder": self.folder.slug, "status": self.kwargs["status"]}
                )
                last_time = results["results"][-1]["ticket"]["last_activity_on"]
                results["next"] = f"{folder_url}?before={datetime_to_timestamp(last_time)}"

            return JsonResponse(results)

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
            assignee = forms.ModelChoiceField(
                queryset=User.objects.none(),
                widget=SelectWidget(attrs={"searchable": True, "widget_only": True}),
                required=False,
                empty_label=_("Unassigned"),
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.org = org
                self.fields["assignee"].queryset = Ticket.get_allowed_assignees(self.org).order_by("email")
                self.fields["note"].required = False

        slug_url_kwarg = "uuid"
        form_class = Form
        fields = ("assignee", "note")
        success_url = "hide"
        success_message = ""
        submit_button_name = _("Save")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.user.get_org()
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            ticket = self.get_object()
            if ticket.assignee:
                initial["assignee"] = ticket.assignee.id
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
        cancel_url = "@orgs.org_home"
        success_url = "@orgs.org_home"
        success_message = _("Your ticketing service has been deleted.")
