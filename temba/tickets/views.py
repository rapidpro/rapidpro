from datetime import timedelta

from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartListView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.db.models.aggregates import Max
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from temba.msgs.models import Msg
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.views import BaseExportView, MenuMixin, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.utils.export import response_from_workbook
from temba.utils.fields import InputWidget
from temba.utils.uuid import UUID_REGEX
from temba.utils.views import ComponentFormMixin, ContentMenuMixin, SpaMixin

from .models import (
    AllFolder,
    MineFolder,
    Ticket,
    TicketCount,
    TicketExport,
    TicketFolder,
    Topic,
    TopicFolder,
    UnassignedFolder,
    export_ticket_stats,
)


class TopicCRUDL(SmartCRUDL):
    model = Topic
    actions = ("create", "update", "delete")
    slug_field = "uuid"

    class Create(OrgPermsMixin, ComponentFormMixin, ModalMixin, SmartCreateView):
        class TopicForm(forms.ModelForm):
            class Meta:
                model = Topic
                fields = ("name",)

        form_class = TopicForm
        success_url = "hide"

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.org
            return obj

    class Update(OrgObjPermsMixin, ComponentFormMixin, ModalMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            def clean_name(self):
                name = self.cleaned_data["name"]

                if self.instance.is_system:
                    raise forms.ValidationError(_("Cannot edit system topic"))

                # make sure the name isn't already taken
                existing = self.instance.org.topics.filter(is_active=True, name__iexact=name).first()
                if existing and self.instance != existing:
                    raise forms.ValidationError(_("Topic already exists, please try another name"))

                return name

            class Meta:
                fields = ("name",)
                model = Topic

        success_url = "hide"
        slug_url_kwarg = "uuid"
        fields = ("name",)
        form_class = Form

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        default_template = "smartmin/delete_confirm.html"
        submit_button_name = _("Delete")
        slug_url_kwarg = "uuid"
        fields = ("uuid",)
        cancel_url = "@tickets.ticket_list"
        redirect_url = "@tickets.ticket_list"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_tickets"] = self.object.tickets.exists()
            return context

        def post(self, request, *args, **kwargs):
            self.get_object().release(self.request.user)
            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self, **kwargs):
            default_topic = self.get_object().org.topics.filter(is_default=True).first()
            return f"/ticket/{str(default_topic.uuid)}/open/"


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("list", "update", "folder", "note", "menu", "export_stats", "export")

    class Update(OrgObjPermsMixin, ComponentFormMixin, ModalMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            def __init__(self, **kwargs):
                super().__init__(**kwargs)

                self.fields["topic"].queryset = self.instance.org.topics.filter(is_active=True).order_by(
                    "-is_system", "name"
                )

            class Meta:
                fields = ("topic",)
                model = Ticket

        form_class = Form
        fields = ("topic",)
        slug_url_kwarg = "uuid"
        success_url = "hide"

    class List(SpaMixin, ContentMenuMixin, OrgPermsMixin, NotificationTargetMixin, SmartListView):
        """
        A placeholder view for the ticket handling frontend components which fetch tickets from the endpoint below
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^ticket/((?P<folder>{folders}|{UUID_REGEX.pattern})/((?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+)/)?)?)?$"

        def get_notification_scope(self) -> tuple:
            folder, status, _, _ = self.tickets_path
            if folder == UnassignedFolder.id and status == "open":
                return "tickets:opened", ""
            elif folder == MineFolder.id and status == "open":
                return "tickets:activity", ""
            return "", ""

        def derive_menu_path(self):
            return f"/ticket/{self.kwargs.get('folder', 'mine')}/"

        @cached_property
        def tickets_path(self) -> tuple:
            """
            Returns tuple of folder, status, ticket uuid, and whether that ticket exists in first page of tickets
            """
            folder = self.kwargs.get("folder")
            status = self.kwargs.get("status")
            uuid = self.kwargs.get("uuid")
            in_page = False

            # if we have a uuid make sure it is in our first page of tickets
            if uuid:
                status_code = Ticket.STATUS_OPEN if status == "open" else Ticket.STATUS_CLOSED
                org = self.request.org
                user = self.request.user
                ticket_folder = TicketFolder.from_id(org, folder)

                if not ticket_folder:
                    raise Http404()

                tickets = list(ticket_folder.get_queryset(org, user, True).filter(status=status_code)[:25])

                found = list(filter(lambda t: str(t.uuid) == uuid, tickets))
                if found:
                    in_page = True
                else:
                    # if it's not, switch our folder to everything with that ticket's state
                    ticket = org.tickets.filter(uuid=uuid).first()
                    if ticket:
                        folder = AllFolder.id
                        status = "open" if ticket.status == Ticket.STATUS_OPEN else "closed"

            return folder or MineFolder.id, status or "open", uuid, in_page

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            folder, status, uuid, in_page = self.tickets_path
            context["folder"] = folder
            context["status"] = status
            context["has_tickets"] = self.request.org.tickets.exists()

            folder = TicketFolder.from_id(self.request.org, folder)
            context["title"] = folder.name

            if uuid:
                context["nextUUID" if in_page else "uuid"] = uuid

            return context

        def build_content_menu(self, menu):
            # we only support dynamic content menus
            if "HTTP_TEMBA_CONTENT_MENU" not in self.request.META:
                return

            uuid = self.kwargs.get("uuid")
            if uuid:
                ticket = self.request.org.tickets.filter(uuid=uuid).first()
                if ticket:
                    if ticket.status == Ticket.STATUS_OPEN:
                        if self.has_org_perm("tickets.ticket_update"):
                            menu.add_modax(
                                _("Edit"),
                                "edit-ticket",
                                f"{reverse('tickets.ticket_update', args=[ticket.uuid])}",
                                title=_("Edit Ticket"),
                                on_submit="handleTicketEditComplete()",
                            )

                        if self.has_org_perm("tickets.ticket_note"):
                            menu.add_modax(
                                _("Add Note"),
                                "add-note",
                                f"{reverse('tickets.ticket_note', args=[ticket.uuid])}",
                                on_submit="handleNoteAdded()",
                            )

                        # we don't want to show start flow if interrupt was given as an option
                        interrupt_added = False
                        if self.has_org_perm("contacts.contact_interrupt") and ticket.contact.current_flow:
                            menu.add_url_post(
                                _("Interrupt"), reverse("contacts.contact_interrupt", args=(ticket.contact.id,))
                            )
                            interrupt_added = True

                        if not interrupt_added and self.has_org_perm("flows.flow_start"):
                            menu.add_modax(
                                _("Start Flow"),
                                "start-flow",
                                f"{reverse('flows.flow_start')}?c={ticket.contact.uuid}",
                                disabled=True,
                                on_submit="handleFlowStarted()",
                            )

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).none()

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):
        def derive_menu(self):
            org = self.request.org
            user = self.request.user
            count_by_assignee = TicketCount.get_by_assignees(org, [None, user], Ticket.STATUS_OPEN)
            counts = {
                MineFolder.id: count_by_assignee[user],
                UnassignedFolder.id: count_by_assignee[None],
                AllFolder.id: TicketCount.get_all(org, Ticket.STATUS_OPEN),
            }

            menu = []
            for folder in TicketFolder.all().values():
                menu.append(
                    {
                        "id": folder.id,
                        "name": folder.name,
                        "icon": folder.icon,
                        "count": counts[folder.id],
                    }
                )

            menu.append(self.create_divider())
            menu.append(self.create_modax_button(_("Export"), "tickets.ticket_export", icon="export"))
            menu.append(
                self.create_modax_button(_("New Topic"), "tickets.topic_create", icon="add", on_submit="refreshMenu()")
            )

            menu.append(self.create_divider())

            topics = list(org.topics.filter(is_active=True).order_by("-is_system", "name"))
            counts = TicketCount.get_by_topics(org, topics, Ticket.STATUS_OPEN)
            for topic in topics:
                menu.append(
                    {
                        "id": topic.uuid,
                        "name": topic.name,
                        "icon": "topic",
                        "count": counts[topic],
                    }
                )

            return menu

    class Folder(ContentMenuMixin, OrgPermsMixin, SmartTemplateView):
        permission = "tickets.ticket_list"
        paginate_by = 25

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^{path}/{action}/(?P<folder>{folders}|{UUID_REGEX.pattern})/(?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+))?$"

        @cached_property
        def folder(self):
            folder = TicketFolder.from_id(self.request.org, self.kwargs["folder"])
            if not folder:
                raise Http404()
            return folder

        def build_content_menu(self, menu):
            # we only support dynamic content menus
            if "HTTP_TEMBA_CONTENT_MENU" not in self.request.META:
                return

            if (
                self.has_org_perm("tickets.topic_update")
                and isinstance(self.folder, TopicFolder)
                and not self.folder.is_system
            ):

                menu.add_modax(
                    _("Edit"),
                    "edit-topic",
                    f"{reverse('tickets.topic_update', args=[self.folder.id])}",
                    title=_("Edit Topic"),
                    on_submit="handleTopicUpdated()",
                )

                menu.add_modax(
                    _("Delete"),
                    "delete-topic",
                    f"{reverse('tickets.topic_delete', args=[self.folder.id])}",
                    title=_("Delete"),
                )

        def get_queryset(self, **kwargs):
            org = self.request.org
            user = self.request.user
            status = Ticket.STATUS_OPEN if self.kwargs["status"] == "open" else Ticket.STATUS_CLOSED
            uuid = self.kwargs.get("uuid", None)
            after = int(self.request.GET.get("after", 0))
            before = int(self.request.GET.get("before", 0))

            # fetching new activity gets a different order later
            ordered = False if after else True
            qs = self.folder.get_queryset(org, user, ordered).filter(status=status)

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
                    qs = self.folder.get_queryset(org, user, ordered).filter(
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
            last_msg_ids = Msg.objects.filter(contact_id__in=contact_ids).values("contact").annotate(last_msg=Max("id"))
            last_msgs = Msg.objects.filter(id__in=[m["last_msg"] for m in last_msg_ids]).select_related("created_by")

            context["last_msgs"] = {m.contact: m for m in last_msgs}
            return context

        def render_to_response(self, context, **response_kwargs):
            def topic_as_json(t):
                return {"uuid": str(t.uuid), "name": t.name}

            def user_as_json(u):
                return {"id": u.id, "first_name": u.first_name, "last_name": u.last_name, "email": u.email}

            def msg_as_json(m):
                return {
                    "text": m.text,
                    "direction": m.direction,
                    "type": m.msg_type,
                    "created_on": m.created_on,
                    "sender": {"id": m.created_by.id, "email": m.created_by.email} if m.created_by else None,
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
                        "last_activity_on": t.last_activity_on,
                        "closed_on": t.closed_on,
                    },
                }

            results = {"results": [as_json(t) for t in context["tickets"]]}

            # build up our next link if we have more
            if len(context["tickets"]) >= self.paginate_by:
                folder_url = reverse(
                    "tickets.ticket_folder", kwargs={"folder": self.folder.id, "status": self.kwargs["status"]}
                )
                last_time = results["results"][-1]["ticket"]["last_activity_on"]
                results["next"] = f"{folder_url}?before={datetime_to_timestamp(last_time)}"

            return JsonResponse(results)

    class Note(ModalMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Creates a note for this contact
        """

        class Form(forms.ModelForm):
            note = forms.CharField(
                max_length=Ticket.MAX_NOTE_LENGTH,
                required=True,
                widget=InputWidget({"hide_label": True, "textarea": True}),
                help_text=_("Notes can only be seen by the support team"),
            )

            class Meta:
                model = Ticket
                fields = ("note",)

        form_class = Form
        fields = ("note",)
        success_url = "hide"
        slug_url_kwarg = "uuid"

        def form_valid(self, form):
            self.get_object().add_note(self.request.user, note=form.cleaned_data["note"])
            return self.render_modal_response(form)

    class ExportStats(OrgPermsMixin, SmartTemplateView):
        def render_to_response(self, context, **response_kwargs):
            num_days = self.request.GET.get("days", 90)
            today = timezone.now().date()
            workbook = export_ticket_stats(
                self.request.org, today - timedelta(days=num_days), today + timedelta(days=1)
            )

            return response_from_workbook(workbook, f"ticket-stats-{timezone.now().strftime('%Y-%m-%d')}.xlsx")

    class Export(BaseExportView):
        export_type = TicketExport
        success_url = "@tickets.ticket_list"

        def create_export(self, org, user, form):
            start_date = form.cleaned_data["start_date"]
            end_date = form.cleaned_data["end_date"]
            with_fields = form.cleaned_data["with_fields"]
            with_groups = form.cleaned_data["with_groups"]
            return TicketExport.create(
                org, user, start_date, end_date, with_fields=with_fields, with_groups=with_groups
            )
