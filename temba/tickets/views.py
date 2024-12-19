from datetime import timedelta

from smartmin.views import SmartCRUDL, SmartListView, SmartTemplateView, SmartUpdateView

from django import forms
from django.db.models.aggregates import Max
from django.db.models.functions import Lower
from django.http import Http404, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _

from temba.msgs.models import Msg
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.models import Org
from temba.orgs.views.base import (
    BaseCreateModal,
    BaseDeleteModal,
    BaseExportModal,
    BaseListView,
    BaseMenuView,
    BaseUpdateModal,
)
from temba.orgs.views.mixins import OrgObjPermsMixin, OrgPermsMixin, RequireFeatureMixin
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.utils.export import response_from_workbook
from temba.utils.fields import InputWidget
from temba.utils.uuid import UUID_REGEX
from temba.utils.views.mixins import ComponentFormMixin, ContextMenuMixin, ModalFormMixin, SpaMixin

from .forms import ShortcutForm, TeamForm, TopicForm
from .models import (
    AllFolder,
    MineFolder,
    Shortcut,
    Team,
    Ticket,
    TicketExport,
    TicketFolder,
    Topic,
    TopicFolder,
    UnassignedFolder,
    export_ticket_stats,
)


class ShortcutCRUDL(SmartCRUDL):
    model = Shortcut
    actions = ("create", "update", "delete", "list")

    class Create(BaseCreateModal):
        form_class = ShortcutForm
        success_url = "@tickets.shortcut_list"

        def save(self, obj):
            return Shortcut.create(self.request.org, self.request.user, obj.name, obj.text)

    class Update(BaseUpdateModal):
        form_class = ShortcutForm
        success_url = "@tickets.shortcut_list"

    class Delete(BaseDeleteModal):
        cancel_url = "@tickets.shortcut_list"
        redirect_url = "@tickets.shortcut_list"

    class List(SpaMixin, ContextMenuMixin, BaseListView):
        menu_path = "/ticket/shortcuts"

        def derive_queryset(self, **kwargs):
            return super().derive_queryset(**kwargs).order_by(Lower("name"))

        def build_context_menu(self, menu):
            if self.has_org_perm("tickets.shortcut_create"):
                menu.add_modax(
                    _("New"),
                    "new-shortcut",
                    reverse("tickets.shortcut_create"),
                    title=_("New Shortcut"),
                    as_button=True,
                )


class TopicCRUDL(SmartCRUDL):
    model = Topic
    actions = ("create", "update", "delete")

    class Create(BaseCreateModal):
        form_class = TopicForm
        success_url = "hide"

        def save(self, obj):
            return Topic.create(self.request.org, self.request.user, obj.name)

    class Update(BaseUpdateModal):
        form_class = TopicForm
        success_url = "hide"

    class Delete(BaseDeleteModal):
        cancel_url = "@tickets.ticket_list"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_tickets"] = self.object.tickets.exists()
            return context

        def get_redirect_url(self, **kwargs):
            return f"/ticket/{self.request.org.default_ticket_topic.uuid}/open/"


class TeamCRUDL(SmartCRUDL):
    model = Team
    actions = ("create", "update", "delete", "list")

    class Create(RequireFeatureMixin, BaseCreateModal):
        require_feature = Org.FEATURE_TEAMS
        form_class = TeamForm
        success_url = "@tickets.team_list"

        def save(self, obj):
            return Team.create(self.request.org, self.request.user, obj.name, topics=self.form.cleaned_data["topics"])

    class Update(BaseUpdateModal):
        form_class = TeamForm
        success_url = "id@orgs.user_team"

    class Delete(BaseDeleteModal):
        cancel_url = "id@orgs.user_team"
        redirect_url = "@tickets.team_list"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_agents"] = self.object.get_users().exists()
            context["has_invitations"] = self.object.invitations.filter(is_active=True).exists()
            return context

    class List(RequireFeatureMixin, SpaMixin, ContextMenuMixin, BaseListView):
        require_feature = Org.FEATURE_TEAMS
        menu_path = "/settings/teams"

        def derive_queryset(self, **kwargs):
            return super().derive_queryset(**kwargs).filter(is_active=True).order_by(Lower("name"))

        def build_context_menu(self, menu):
            if self.has_org_perm("tickets.team_create"):
                menu.add_modax(
                    _("New"),
                    "new-team",
                    reverse("tickets.team_create"),
                    title=_("New Team"),
                    as_button=True,
                )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # annotate each team with its user count
            for team in context["object_list"]:
                team.user_count = team.get_users().count()

            return context


class TicketCRUDL(SmartCRUDL):
    model = Ticket
    actions = ("menu", "list", "folder", "update", "note", "export_stats", "export")

    class Menu(BaseMenuView):
        def derive_menu(self):
            org = self.request.org
            user = self.request.user
            topics = Topic.get_accessible(org, user).order_by("-is_system", "name")
            counts = {
                MineFolder.slug: Ticket.get_assignee_count(org, user, topics, Ticket.STATUS_OPEN),
                UnassignedFolder.slug: Ticket.get_assignee_count(org, None, topics, Ticket.STATUS_OPEN),
                AllFolder.slug: Ticket.get_status_count(org, topics, Ticket.STATUS_OPEN),
            }

            menu = []
            for folder in TicketFolder.all().values():
                menu.append(
                    {
                        "id": folder.slug,
                        "name": folder.name,
                        "icon": folder.get_icon(counts[folder.slug]),
                        "count": counts[folder.slug],
                        "href": f"/ticket/{folder.slug}/open/",
                    }
                )

            menu.append(self.create_divider())
            menu.append(
                self.create_menu_item(
                    menu_id="shortcuts",
                    name=_("Shortcuts"),
                    icon="shortcut",
                    count=org.shortcuts.filter(is_active=True).count(),
                    href="tickets.shortcut_list",
                )
            )
            menu.append(self.create_modax_button(_("Export"), "tickets.ticket_export", icon="export"))
            menu.append(
                self.create_modax_button(_("New Topic"), "tickets.topic_create", icon="add", on_submit="refreshMenu()")
            )

            menu.append(self.create_divider())

            counts = Ticket.get_topic_counts(org, topics, Ticket.STATUS_OPEN)
            for topic in topics:
                menu.append(
                    {
                        "id": topic.uuid,
                        "name": topic.name,
                        "icon": "topic",
                        "count": counts[topic],
                        "href": f"/ticket/{topic.uuid}/open/",
                    }
                )

            return menu

    class List(SpaMixin, ContextMenuMixin, OrgPermsMixin, NotificationTargetMixin, SmartListView):
        """
        Placeholder view for the ticketing frontend components which fetch tickets from the folders view below.
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^ticket/((?P<folder>{folders}|{UUID_REGEX.pattern})/((?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+)/)?)?)?$"

        def get_notification_scope(self) -> tuple:
            folder, status, ticket, in_page = self.tickets_path

            if folder.slug == UnassignedFolder.slug and status == Ticket.STATUS_OPEN:
                return "tickets:opened", ""
            elif folder.slug == MineFolder.slug and status == Ticket.STATUS_OPEN:
                return "tickets:activity", ""
            return "", ""

        def derive_menu_path(self):
            folder, status, ticket, in_page = self.tickets_path

            return f"/ticket/{folder.slug}/"

        @cached_property
        def tickets_path(self) -> tuple[TicketFolder, str, Ticket, bool]:
            """
            Returns tuple of folder, status, ticket, and whether that ticket exists in first page of tickets
            """

            org = self.request.org
            user = self.request.user

            # get requested folder, defaulting to Mine
            folder = TicketFolder.from_slug(org, user, self.kwargs.get("folder", MineFolder.slug))
            if not folder:
                raise Http404()

            status = Ticket.STATUS_OPEN if self.kwargs.get("status", "open") == "open" else Ticket.STATUS_CLOSED
            ticket = None
            in_page = False

            # is the request for a specific ticket?
            if uuid := self.kwargs.get("uuid"):
                # is the ticket in the first page from of current folder?
                for t in list(folder.get_queryset(org, user, ordered=True).filter(status=status)[:25]):
                    if str(t.uuid) == uuid:
                        ticket = t
                        in_page = True
                        break

                # if not, see if we can access it in the All tickets folder and if so switch to that
                if not in_page:
                    all_folder = TicketFolder.from_slug(org, user, AllFolder.slug)
                    ticket = all_folder.get_queryset(org, user, ordered=False).filter(uuid=uuid).first()

                    if ticket:
                        folder = all_folder
                        status = ticket.status

            return folder, status, ticket, in_page

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            folder, status, ticket, in_page = self.tickets_path

            context["title"] = folder.name
            context["folder"] = str(folder.slug)
            context["status"] = "open" if status == Ticket.STATUS_OPEN else "closed"
            context["has_tickets"] = self.request.org.tickets.exists()

            if ticket:
                context["nextUUID" if in_page else "uuid"] = str(ticket.uuid)

            return context

        def build_context_menu(self, menu):
            folder, status, ticket, in_page = self.tickets_path

            if ticket and ticket.status == Ticket.STATUS_OPEN:
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

                if not ticket.contact.current_flow:
                    if self.has_org_perm("flows.flow_start"):
                        menu.add_modax(
                            _("Start Flow"),
                            "start-flow",
                            f"{reverse('flows.flow_start')}?c={ticket.contact.uuid}",
                            disabled=True,
                            on_submit="handleFlowStarted()",
                        )

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).none()

    class Folder(ContextMenuMixin, OrgPermsMixin, SmartTemplateView):
        permission = "tickets.ticket_list"
        paginate_by = 25

        @classmethod
        def derive_url_pattern(cls, path, action):
            folders = "|".join(TicketFolder.all().keys())
            return rf"^{path}/{action}/(?P<folder>{folders}|{UUID_REGEX.pattern})/(?P<status>open|closed)/((?P<uuid>[a-z0-9\-]+))?$"

        @cached_property
        def folder(self) -> TicketFolder:
            folder = TicketFolder.from_slug(self.request.org, self.request.user, self.kwargs["folder"])
            if not folder:
                raise Http404()

            return folder

        def build_context_menu(self, menu):
            if isinstance(self.folder, TopicFolder) and not self.folder.topic.is_system:
                if self.has_org_perm("tickets.topic_update"):
                    menu.add_modax(
                        _("Edit"),
                        "edit-topic",
                        f"{reverse('tickets.topic_update', args=[self.folder.topic.id])}",
                        title=_("Edit Topic"),
                        on_submit="handleTopicUpdated()",
                    )
                if self.has_org_perm("tickets.topic_delete"):
                    menu.add_modax(
                        _("Delete"),
                        "delete-topic",
                        f"{reverse('tickets.topic_delete', args=[self.folder.topic.id])}",
                        title=_("Delete Topic"),
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
            qs = self.folder.get_queryset(org, user, ordered=ordered).filter(status=status)

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
                    qs = self.folder.get_queryset(org, user, ordered=ordered).filter(
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

            context["last_msgs"] = {m.contact_id: m for m in last_msgs}
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
                last_msg = context["last_msgs"].get(t.contact_id)
                return {
                    "uuid": str(t.contact.uuid),
                    "name": t.contact.get_display(org=self.request.org),
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
                    "tickets.ticket_folder", kwargs={"folder": self.folder.slug, "status": self.kwargs["status"]}
                )
                last_time = results["results"][-1]["ticket"]["last_activity_on"]
                results["next"] = f"{folder_url}?before={datetime_to_timestamp(last_time)}"

            return JsonResponse(results)

    class Update(ComponentFormMixin, ModalFormMixin, OrgObjPermsMixin, SmartUpdateView):
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

    class Note(ModalFormMixin, ComponentFormMixin, OrgObjPermsMixin, SmartUpdateView):
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

    class Export(BaseExportModal):
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
