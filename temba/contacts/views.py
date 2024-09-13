import logging
from collections import OrderedDict
from datetime import timedelta
from urllib.parse import quote_plus

import iso8601
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
    SmartView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator
from django.db import transaction
from django.db.models.functions import Upper
from django.http import Http404, HttpResponse, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views import View

from temba import mailroom
from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.mailroom.events import Event
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.models import User
from temba.orgs.views import (
    BaseExportView,
    DependencyDeleteModal,
    DependencyUsagesModal,
    MenuMixin,
    ModalMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.tickets.models import Ticket, Topic
from temba.utils import json, on_transaction_commit
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.utils.fields import CheckboxWidget, InputWidget, SelectWidget, TembaChoiceField
from temba.utils.models import patch_queryset_count
from temba.utils.models.es import IDSliceQuerySet
from temba.utils.views import BulkActionMixin, ComponentFormMixin, ContentMenuMixin, NonAtomicMixin, SpaMixin

from .forms import ContactGroupForm, CreateContactForm, UpdateContactForm
from .models import URN, Contact, ContactExport, ContactField, ContactGroup, ContactGroupCount, ContactImport
from .omnibox import omnibox_query, omnibox_serialize

logger = logging.getLogger(__name__)

# events from sessions to include in contact history
HISTORY_INCLUDE_EVENTS = {
    Event.TYPE_CONTACT_LANGUAGE_CHANGED,
    Event.TYPE_CONTACT_FIELD_CHANGED,
    Event.TYPE_CONTACT_GROUPS_CHANGED,
    Event.TYPE_CONTACT_NAME_CHANGED,
    Event.TYPE_CONTACT_URNS_CHANGED,
}


class ContactListView(SpaMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
    """
    Base class for contact list views with contact folders and groups listed by the side
    """

    permission = "contacts.contact_list"
    system_group = None
    add_button = True
    paginate_by = 50

    parsed_query = None
    save_dynamic_search = None

    sort_field = None
    sort_direction = None

    search_error = None

    def pre_process(self, request, *args, **kwargs):
        """
        Don't allow pagination past 200th page
        """
        if int(self.request.GET.get("page", "1")) > 200:
            return HttpResponseNotFound()

        return super().pre_process(request, *args, **kwargs)

    @cached_property
    def group(self):
        return self.derive_group()

    def derive_group(self):
        return self.request.org.groups.get(group_type=self.system_group)

    def derive_export_url(self):
        search = quote_plus(self.request.GET.get("search", ""))
        return f"{reverse('contacts.contact_export')}?g={self.group.uuid}&s={search}"

    def derive_refresh(self):
        # smart groups that are reevaluating should refresh every 2 seconds
        if self.group.is_smart and self.group.status != ContactGroup.STATUS_READY:
            return 200000

        return None

    def get_queryset(self, **kwargs):
        org = self.request.org
        self.search_error = None

        # contact list views don't use regular field searching but use more complex contact searching
        search_query = self.request.GET.get("search", None)
        sort_on = self.request.GET.get("sort_on", "")
        page = self.request.GET.get("page", "1")

        offset = (int(page) - 1) * 50

        self.sort_direction = "desc" if sort_on.startswith("-") else "asc"
        self.sort_field = sort_on.lstrip("-")

        if search_query or sort_on:
            # is this request is part of a bulk action, get the ids that were modified so we can check which ones
            # should no longer appear in this view, even though ES won't have caught up yet
            bulk_action_ids = self.kwargs.get("bulk_action_ids", [])
            if bulk_action_ids:
                reappearing_ids = set(self.group.contacts.filter(id__in=bulk_action_ids).values_list("id", flat=True))
                exclude_ids = [i for i in bulk_action_ids if i not in reappearing_ids]
            else:
                exclude_ids = []

            try:
                results = mailroom.get_client().contact_search(
                    org, self.group, search_query, sort=sort_on, offset=offset, exclude_ids=exclude_ids
                )
                self.parsed_query = results.query if len(results.query) > 0 else None
                self.save_dynamic_search = results.metadata.allow_as_group

                return IDSliceQuerySet(Contact, results.contact_ids, offset=offset, total=results.total)
            except mailroom.QueryValidationException as e:
                self.search_error = str(e)

                # this should be an empty resultset
                return Contact.objects.none()
        else:
            # if user search is not defined, use DB to select contacts
            qs = self.group.contacts.filter(org=self.request.org).order_by("-id").prefetch_related("org", "groups")
            patch_queryset_count(qs, self.group.get_member_count)
            return qs

    def get_bulk_action_labels(self):
        return ContactGroup.get_groups(self.request.org, manual_only=True)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        org = self.request.org

        # resolve the paginated object list so we can initialize a cache of URNs
        contacts = context["object_list"]
        Contact.bulk_urn_cache_initialize(contacts)

        context["contacts"] = contacts
        context["has_contacts"] = contacts or org.get_contact_count() > 0
        context["search_error"] = self.search_error

        context["sort_direction"] = self.sort_direction
        context["sort_field"] = self.sort_field

        # replace search string with parsed search expression
        if self.parsed_query is not None:
            context["search"] = self.parsed_query
            context["save_dynamic_search"] = self.save_dynamic_search

        return context


class ContactCRUDL(SmartCRUDL):
    model = Contact
    actions = (
        "create",
        "update",
        "search",
        "stopped",
        "archived",
        "list",
        "menu",
        "read",
        "filter",
        "blocked",
        "omnibox",
        "open_ticket",
        "export",
        "interrupt",
        "delete",
        "scheduled",
        "history",
    )

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):
        def render_to_response(self, context, **response_kwargs):
            org = self.request.org
            counts = Contact.get_status_counts(org)
            menu = [
                {
                    "id": "active",
                    "count": counts[Contact.STATUS_ACTIVE],
                    "name": _("Active"),
                    "href": reverse("contacts.contact_list"),
                    "icon": "active",
                },
                {
                    "id": "archived",
                    "icon": "archive",
                    "count": counts[Contact.STATUS_ARCHIVED],
                    "name": _("Archived"),
                    "href": reverse("contacts.contact_archived"),
                },
                {
                    "id": "blocked",
                    "count": counts[Contact.STATUS_BLOCKED],
                    "name": _("Blocked"),
                    "href": reverse("contacts.contact_blocked"),
                    "icon": "contact_blocked",
                },
                {
                    "id": "stopped",
                    "count": counts[Contact.STATUS_STOPPED],
                    "name": _("Stopped"),
                    "href": reverse("contacts.contact_stopped"),
                    "icon": "contact_stopped",
                },
            ]

            menu.append(self.create_divider())
            menu.append(
                {
                    "id": "import",
                    "icon": "upload",
                    "href": reverse("contacts.contactimport_create"),
                    "name": _("Import"),
                }
            )

            if self.has_org_perm("contacts.contactfield_list"):
                menu.append(
                    dict(
                        id="fields",
                        icon="fields",
                        count=ContactField.get_fields(org).count(),
                        name=_("Fields"),
                        href=reverse("contacts.contactfield_list"),
                    )
                )

            groups = (
                ContactGroup.get_groups(org, ready_only=False)
                .select_related("org")
                .order_by("-group_type", Upper("name"))
            )
            group_counts = ContactGroupCount.get_totals(groups)
            group_items = []

            for g in groups:
                group_items.append(
                    self.create_menu_item(
                        menu_id=g.uuid,
                        name=g.name,
                        icon=g.icon,
                        count=group_counts[g],
                        href=reverse("contacts.contact_filter", args=[g.uuid]),
                    )
                )

            if group_items:
                menu.append(
                    {"id": "filter", "icon": "users", "name": _("Groups"), "items": group_items, "inline": True}
                )

            return JsonResponse({"results": menu})

    class Export(BaseExportView):
        export_type = ContactExport
        success_url = "@contacts.contact_list"
        size_limit = 1_000_000

        def derive_fields(self):
            return ("with_groups",)

        def get_blocker(self) -> str:
            if blocker := super().get_blocker():
                return blocker

            query = self.request.GET.get("s")
            total = mailroom.get_client().contact_export_preview(self.request.org, self.group, query)
            if total > self.size_limit:
                return "too-big"

            return ""

        @cached_property
        def group(self):
            org = self.request.org
            group_uuid = self.request.GET.get("g")
            return org.groups.filter(uuid=group_uuid).first() if group_uuid else org.active_contacts_group

        def create_export(self, org, user, form):
            search = self.request.GET.get("s")
            with_groups = form.cleaned_data["with_groups"]
            return ContactExport.create(org, user, group=self.group, search=search, with_groups=with_groups)

    class Omnibox(OrgPermsMixin, SmartListView):
        def get_queryset(self, **kwargs):
            return Contact.objects.none()

        def render_to_response(self, context, **response_kwargs):
            org = self.request.org
            groups, contacts = omnibox_query(org, **{k: v for k, v in self.request.GET.items()})
            results = omnibox_serialize(org, groups, contacts)

            return JsonResponse({"results": results, "more": False, "total": len(results), "err": "nil"})

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("name",)
        select_related = ("current_flow",)

        def derive_menu_path(self):
            return f"/contact/{self.object.get_status_display().lower()}"

        def derive_title(self):
            return self.object.get_display()

        def get_queryset(self):
            return Contact.objects.filter(is_active=True)

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("contacts.contact_update"):
                menu.add_modax(
                    _("Edit"),
                    "edit-contact",
                    f"{reverse('contacts.contact_update', args=[obj.id])}",
                    title=_("Edit Contact"),
                    on_submit="contactUpdated()",
                    as_button=True,
                )

            if obj.status == Contact.STATUS_ACTIVE:
                if self.has_org_perm("flows.flow_start"):
                    menu.add_modax(
                        _("Start Flow"),
                        "start-flow",
                        f"{reverse('flows.flow_start')}?c={obj.uuid}",
                        on_submit="contactUpdated()",
                        disabled=True,
                    )
                if self.has_org_perm("contacts.contact_open_ticket") and obj.ticket_count == 0:
                    menu.add_modax(
                        _("Open Ticket"), "open-ticket", reverse("contacts.contact_open_ticket", args=[obj.id])
                    )
                if self.has_org_perm("contacts.contact_interrupt") and obj.current_flow:
                    menu.add_url_post(_("Interrupt"), reverse("contacts.contact_interrupt", args=(obj.id,)))

    class Scheduled(OrgObjPermsMixin, SmartReadView):
        """
        Merged list of upcoming scheduled events (campaign event fires and scheduled broadcasts)
        """

        permission = "contacts.contact_read"
        slug_url_kwarg = "uuid"

        def get_queryset(self):
            return Contact.objects.filter(is_active=True).select_related("org")

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse({"results": self.object.get_scheduled()})

    class History(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_queryset(self):
            return Contact.objects.filter(is_active=True).select_related("org")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            contact = self.get_object()

            # since we create messages with timestamps from external systems, always a chance a contact's initial
            # message has a timestamp slightly earlier than the contact itself.
            contact_creation = contact.created_on - timedelta(hours=1)

            before = int(self.request.GET.get("before", 0))
            after = int(self.request.GET.get("after", 0))
            limit = int(self.request.GET.get("limit", 50))

            ticket_uuid = self.request.GET.get("ticket")
            ticket = contact.org.tickets.filter(uuid=ticket_uuid).first()

            # if we want an expanding window, or just all the recent activity
            recent_only = False
            if not before:
                recent_only = True
                before = timezone.now()
            else:
                before = timestamp_to_datetime(before)

            if not after:
                after = before - timedelta(days=90)
            else:
                after = timestamp_to_datetime(after)

            # keep looking further back until we get at least 20 items
            history = []
            fetch_before = before
            while True:
                history += contact.get_history(after, fetch_before, HISTORY_INCLUDE_EVENTS, ticket=ticket, limit=limit)
                if recent_only or len(history) >= 20 or after == contact_creation:
                    break
                else:
                    fetch_before = after
                    after = max(after - timedelta(days=90), contact_creation)

            # render as events
            events = [Event.from_history_item(contact.org, self.request.user, i) for i in history]

            if len(events) >= limit:
                after = iso8601.parse_date(events[-1]["created_on"])

            # check if there are more pages to fetch
            context["has_older"] = False
            if not recent_only and before > contact.created_on:
                context["has_older"] = bool(
                    contact.get_history(contact_creation, after, HISTORY_INCLUDE_EVENTS, ticket=ticket, limit=1)
                )

            context["recent_only"] = recent_only
            context["next_before"] = datetime_to_timestamp(after)
            context["next_after"] = datetime_to_timestamp(max(after - timedelta(days=90), contact_creation))
            context["start_date"] = contact.org.get_delete_date(archive_type=Archive.TYPE_MSG)
            context["events"] = events
            return context

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse(
                {
                    "has_older": context["has_older"],
                    "recent_only": context["recent_only"],
                    "next_before": context["next_before"],
                    "next_after": context["next_after"],
                    "start_date": context["start_date"],
                    "events": context["events"],
                }
            )

    class Search(ContactListView):
        template_name = "contacts/contact_list.html"

        def get(self, request, *args, **kwargs):
            org = self.request.org
            query = self.request.GET.get("search", None)
            samples = int(self.request.GET.get("samples", 10))

            if not query:
                return JsonResponse({"total": 0, "sample": [], "fields": {}})

            try:
                results = mailroom.get_client().contact_search(
                    org, org.active_contacts_group, query, sort="-created_on"
                )
                summary = {
                    "total": results.total,
                    "query": results.query,
                    "fields": results.metadata.fields,
                    "sample": IDSliceQuerySet(Contact, results.contact_ids, offset=0, total=results.total)[0:samples],
                }
            except mailroom.QueryValidationException as e:
                return JsonResponse({"total": 0, "sample": [], "query": "", "error": str(e)})

            # serialize our contact sample
            json_contacts = []
            for contact in summary["sample"]:
                primary_urn = contact.get_urn()
                if primary_urn:
                    primary_urn = primary_urn.get_display(org=org, international=True)
                else:
                    primary_urn = "--"

                contact_json = {
                    "name": contact.name,
                    "fields": contact.fields if contact.fields else {},
                    "primary_urn_formatted": primary_urn,
                }
                contact_json["created_on"] = org.format_datetime(contact.created_on, show_time=False)
                contact_json["last_seen_on"] = org.format_datetime(contact.last_seen_on, show_time=False)

                json_contacts.append(contact_json)
            summary["sample"] = json_contacts

            # add in our field defs
            field_keys = [f["key"] for f in summary["fields"]]
            summary["fields"] = {
                str(f.uuid): {"label": f.name}
                for f in org.fields.filter(key__in=field_keys, is_active=True, is_proxy=False)
            }
            return JsonResponse(summary)

    class List(ContentMenuMixin, ContactListView):
        title = _("Active")
        system_group = ContactGroup.TYPE_DB_ACTIVE
        menu_path = "/contact/active"

        def get_bulk_actions(self):
            actions = ("block", "archive") if self.has_org_perm("contacts.contact_update") else ()
            if self.has_org_perm("msgs.broadcast_create"):
                actions += ("send",)
            if self.has_org_perm("flows.flow_start"):
                actions += ("start-flow",)
            return actions

        def build_content_menu(self, menu):
            search = self.request.GET.get("search")

            # define save search conditions
            valid_search_condition = search and not self.search_error
            has_contactgroup_create_perm = self.has_org_perm("contacts.contactgroup_create")

            if has_contactgroup_create_perm and valid_search_condition:
                try:
                    parsed = mailroom.get_client().contact_parse_query(self.request.org, search)
                    if parsed.metadata.allow_as_group:
                        menu.add_modax(
                            _("Create Smart Group"),
                            "create-smartgroup",
                            f"{reverse('contacts.contactgroup_create')}?search={quote_plus(search)}",
                            as_button=True,
                        )
                except mailroom.QueryValidationException:  # pragma: no cover
                    pass

            if self.has_org_perm("contacts.contact_create"):
                menu.add_modax(
                    _("New Contact"), "new-contact", reverse("contacts.contact_create"), title=_("New Contact")
                )

            if has_contactgroup_create_perm:
                menu.add_modax(
                    _("New Group"), "new-group", reverse("contacts.contactgroup_create"), title=_("New Group")
                )

            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.org

            context["contact_fields"] = ContactField.get_fields(org).order_by("-show_in_table", "-priority", "id")[0:6]
            return context

    class Blocked(ContentMenuMixin, ContactListView):
        title = _("Blocked")
        system_group = ContactGroup.TYPE_DB_BLOCKED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def build_content_menu(self, menu):
            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Stopped(ContentMenuMixin, ContactListView):
        title = _("Stopped")
        template_name = "contacts/contact_stopped.html"
        system_group = ContactGroup.TYPE_DB_STOPPED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def build_content_menu(self, menu):
            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Archived(ContentMenuMixin, ContactListView):
        title = _("Archived")
        template_name = "contacts/contact_archived.html"
        system_group = ContactGroup.TYPE_DB_ARCHIVED
        bulk_action_permissions = {"delete": "contacts.contact_delete"}

        def get_bulk_actions(self):
            actions = []
            if self.has_org_perm("contacts.contact_update"):
                actions.append("restore")
            if self.has_org_perm("contacts.contact_delete"):
                actions.append("delete")
            return actions

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

        def build_content_menu(self, menu):
            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

            if self.has_org_perm("contacts.contact_delete"):
                menu.add_js("contacts_delete_all", _("Delete All"))

    class Filter(OrgObjPermsMixin, ContentMenuMixin, ContactListView):
        template_name = "contacts/contact_filter.html"

        def build_content_menu(self, menu):
            if not self.group.is_system and self.has_org_perm("contacts.contactgroup_update"):
                menu.add_modax(_("Edit"), "edit-group", reverse("contacts.contactgroup_update", args=[self.group.id]))

            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

            menu.add_modax(_("Usages"), "group-usages", reverse("contacts.contactgroup_usages", args=[self.group.uuid]))

            if not self.group.is_system and self.has_org_perm("contacts.contactgroup_delete"):
                menu.add_modax(
                    _("Delete"), "delete-group", reverse("contacts.contactgroup_delete", args=[self.group.uuid])
                )

        def get_bulk_actions(self):
            actions = ()
            if self.has_org_perm("contacts.contact_update"):
                actions += ("block", "archive") if self.group.is_smart else ("block", "unlabel")
            if self.has_org_perm("msgs.broadcast_create"):
                actions += ("send",)
            if self.has_org_perm("flows.flow_start"):
                actions += ("start-flow",)
            return actions

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.org

            context["current_group"] = self.group
            context["contact_fields"] = ContactField.get_fields(org).order_by("-priority", "id")
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<group>[^/]+)/$" % (path, action)

        def get_object_org(self):
            return self.group.org

        def derive_title(self):
            return self.group.name

        def derive_group(self):
            try:
                return ContactGroup.objects.get(
                    is_active=True,
                    group_type__in=(ContactGroup.TYPE_MANUAL, ContactGroup.TYPE_SMART),
                    uuid=self.kwargs["group"],
                )
            except ContactGroup.DoesNotExist:
                raise Http404("Group not found")

    class Create(NonAtomicMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = CreateContactForm
        submit_button_name = _("Create")

        def get_form_kwargs(self, *args, **kwargs):
            kwargs = super().get_form_kwargs(*args, **kwargs)
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            name = self.form.cleaned_data.get("name")
            phone = self.form.cleaned_data.get("phone")
            urns = ["tel:" + phone] if phone else []

            try:
                Contact.create(
                    self.request.org,
                    self.request.user,
                    name=name,
                    language="",
                    status=Contact.STATUS_ACTIVE,
                    urns=urns,
                    fields={},
                    groups=[],
                )
            except mailroom.URNValidationException as e:  # pragma: needs cover
                error = _("In use by another contact.") if e.code == "taken" else _("Not a valid phone number.")
                self.form.add_error("phone", error)
                return self.form_invalid(form)

            return self.render_modal_response(form)

    class Update(SpaMixin, ComponentFormMixin, NonAtomicMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateContactForm
        success_url = "hide"

        def derive_exclude(self):
            obj = self.get_object()
            exclude = []
            exclude.extend(self.exclude)

            if obj.status != Contact.STATUS_ACTIVE:
                exclude.append("groups")

            return exclude

        def get_form_kwargs(self, *args, **kwargs):
            kwargs = super().get_form_kwargs(*args, **kwargs)
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["schemes"] = URN.SCHEME_CHOICES
            return context

        def form_valid(self, form):
            obj = self.get_object()
            data = form.cleaned_data
            user = self.request.user

            status = data.get("status")
            if status and status != obj.status:
                if status == Contact.STATUS_ACTIVE:
                    obj.restore(user)
                elif status == Contact.STATUS_ARCHIVED:
                    obj.archive(user)
                elif status == Contact.STATUS_BLOCKED:
                    obj.block(user)
                elif status == Contact.STATUS_STOPPED:
                    obj.stop(user)

            mods = obj.update(data.get("name"), data.get("language"))

            new_groups = self.form.cleaned_data.get("groups")
            if new_groups is not None:
                mods += obj.update_static_groups(new_groups)

            if not obj.org.is_anon:
                urns = []

                for field_key, value in self.form.data.items():
                    if field_key.startswith("urn__") and value:
                        parts = field_key.split("__")
                        scheme = parts[1]

                        order = int(self.form.data.get("order__" + field_key, "0"))
                        urns.append((order, URN.from_parts(scheme, value)))

                new_scheme = data.get("new_scheme", None)
                new_path = data.get("new_path", None)

                if new_scheme and new_path:
                    urns.append((len(urns), URN.from_parts(new_scheme, new_path)))

                # sort our urns by the supplied order
                urns = [urn[1] for urn in sorted(urns, key=lambda x: x[0])]
                mods += obj.update_urns(urns)

            try:
                obj.modify(self.request.user, mods)
            except Exception:
                errors = form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.utils.ErrorList())
                errors.append(_("An error occurred updating your contact. Please try again later."))
                return self.render_to_response(self.get_context_data(form=form))

            messages.success(self.request, self.derive_success_message())

            return self.render_modal_response(form)

    class OpenTicket(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Opens a new ticket for this contact.
        """

        class Form(forms.Form):
            topic = forms.ModelChoiceField(queryset=Topic.objects.none(), label=_("Topic"), required=True)
            assignee = forms.ModelChoiceField(
                queryset=User.objects.none(),
                label=_("Assignee"),
                widget=SelectWidget(),
                required=False,
                empty_label=_("Unassigned"),
            )
            note = forms.CharField(
                label=_("Note"),
                widget=InputWidget(attrs={"textarea": True, "placeholder": _("Optional")}),
                required=False,
            )

            def __init__(self, instance, org, **kwargs):
                super().__init__(**kwargs)

                self.fields["topic"].queryset = org.topics.filter(is_active=True).order_by("name")
                self.fields["assignee"].queryset = Ticket.get_allowed_assignees(org).order_by("email")

        form_class = Form
        submit_button_name = _("Open")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def save(self, obj):
            self.ticket = obj.open_ticket(
                self.request.user,
                topic=self.form.cleaned_data["topic"],
                assignee=self.form.cleaned_data.get("assignee"),
                note=self.form.cleaned_data.get("note"),
            )

        def get_success_url(self):
            return f"{reverse('tickets.ticket_list')}all/open/{self.ticket.uuid}/"

    class Interrupt(OrgObjPermsMixin, SmartUpdateView):
        """
        Interrupt this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"

        def save(self, obj):
            obj.interrupt(self.request.user)
            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Delete this contact (can't be undone)
        """

        fields = ()
        success_url = "@contacts.contact_list"
        submit_button_name = _("Delete")

        def save(self, obj):
            obj.release(self.request.user)
            return obj


class ContactGroupCRUDL(SmartCRUDL):
    model = ContactGroup
    actions = ("create", "update", "usages", "delete", "menu")

    class Menu(MenuMixin, OrgPermsMixin, SmartTemplateView):  # pragma: no cover
        def derive_menu(self):
            org = self.request.org

            # order groups with smart (group_type=Q) before manual (group_type=M)
            all_groups = ContactGroup.get_groups(org).order_by("-group_type", Upper("name"))
            group_counts = ContactGroupCount.get_totals(all_groups)

            menu = []
            for g in all_groups:
                menu.append(
                    self.create_menu_item(
                        menu_id=g.uuid,
                        name=g.name,
                        icon="loader" if g.status != ContactGroup.STATUS_READY else "atom" if g.query else "",
                        count=group_counts[g],
                        href=reverse("contacts.contact_filter", args=[g.uuid]),
                    )
                )
            return menu

    class Create(ComponentFormMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactGroupForm
        fields = ("name", "preselected_contacts", "group_query")
        success_url = "uuid@contacts.contact_filter"
        submit_button_name = _("Create")

        def save(self, obj):
            org = self.request.org
            user = self.request.user
            name = self.form.cleaned_data.get("name")
            query = self.form.cleaned_data.get("group_query")
            preselected_contacts = self.form.cleaned_data.get("preselected_contacts")

            if query:
                self.object = ContactGroup.create_smart(org, user, name, query)
            else:
                self.object = ContactGroup.create_manual(org, user, name)

                if preselected_contacts:
                    preselected_ids = [int(c_id) for c_id in preselected_contacts.split(",") if c_id.isdigit()]
                    contacts = org.contacts.filter(id__in=preselected_ids, is_active=True)

                    on_transaction_commit(lambda: Contact.bulk_change_group(user, contacts, self.object, add=True))

        def derive_initial(self):
            initial = super().derive_initial()
            initial["group_query"] = self.request.GET.get("search", "")
            return initial

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

    class Update(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = ContactGroupForm
        fields = ("name",)
        success_url = "uuid@contacts.contact_filter"

        def get_queryset(self):
            return super().get_queryset().filter(is_system=False)

        def derive_fields(self):
            return ("name", "query") if self.get_object().is_smart else ("name",)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            self.prev_query = self.get_object().query

            return super().form_valid(form)

        def post_save(self, obj):
            obj = super().post_save(obj)

            if obj.query and obj.query != self.prev_query:
                obj.update_query(obj.query)
            return obj

    class Usages(DependencyUsagesModal):
        permission = "contacts.contactgroup_read"

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@contacts.contact_filter"
        success_url = "@contacts.contact_list"


class ContactFieldForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

        is_already_location_type = self.instance and self.instance.value_type in (
            ContactField.TYPE_STATE,
            ContactField.TYPE_DISTRICT,
            ContactField.TYPE_WARD,
        )
        allow_location_types = "locations" in settings.FEATURES or is_already_location_type
        self.fields["value_type"].choices = (
            ContactField.TYPE_CHOICES if allow_location_types else ContactField.TYPE_CHOICES_BASIC
        )

    def clean_name(self):
        name = self.cleaned_data["name"]

        if not ContactField.is_valid_name(name):
            raise forms.ValidationError(_("Can only contain letters, numbers and hypens."))

        if not ContactField.is_valid_key(ContactField.make_key(name)):
            raise forms.ValidationError(_("Can't be a reserved word."))

        conflict = self.org.fields.filter(is_active=True, name__iexact=name.lower())
        if self.instance:
            conflict = conflict.exclude(id=self.instance.id)

        if conflict.exists():
            raise forms.ValidationError(_("Must be unique."))

        return name

    def clean_value_type(self):
        value_type = self.cleaned_data["value_type"]

        if self.instance and self.instance.id and self.instance.campaign_events.filter(is_active=True).exists():
            if value_type != ContactField.TYPE_DATETIME:
                raise forms.ValidationError(_("Can't change type of date field being used by campaign events."))

        return value_type

    class Meta:
        model = ContactField
        fields = ("name", "value_type", "show_in_table", "agent_access")
        labels = {
            "name": _("Name"),
            "value_type": _("Data Type"),
            "show_in_table": _("Featured"),
            "agent_access": _("Agent Access"),
        }
        help_texts = {
            "value_type": _("Type of the values that will be stored in this field."),
            "agent_access": _("Type of access that agent users have for this field."),
        }
        widgets = {
            "name": InputWidget(attrs={"widget_only": False}),
            "value_type": SelectWidget(attrs={"widget_only": False}),
            "show_in_table": CheckboxWidget(attrs={"widget_only": True}),
            "agent_access": SelectWidget(attrs={"widget_only": False}),
        }


class FieldLookupMixin:
    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/(?P<key>[^/]+)/$" % (path, action)

    def has_permission(self, request, *args, **kwargs):
        object = self.get_object()
        if object:
            return super().has_permission(request, *args, **kwargs)
        return False

    def get_object(self):
        if self.request.org:
            return self.request.org.fields.filter(key=self.kwargs["key"], is_active=True).first()
        return None


class ContactFieldCRUDL(SmartCRUDL):
    model = ContactField
    actions = ("list", "create", "update", "update_priority", "delete", "usages")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        class Form(ContactFieldForm):
            def clean(self):
                super().clean()

                count, limit = ContactField.get_org_limit_progress(self.org)
                if limit is not None and count >= limit:
                    raise forms.ValidationError(
                        _(
                            "This workspace has reached its limit of %(limit)d fields. "
                            "You must delete existing ones before you can create new ones."
                        ),
                        params={"limit": limit},
                    )

        queryset = ContactField.user_fields
        form_class = Form
        success_url = "hide"
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context_data = super().get_context_data(**kwargs)
            org_count, org_limit = ContactField.get_org_limit_progress(self.request.org)
            context_data["total_count"] = org_count
            context_data["total_limit"] = org_limit
            return context_data

        def form_valid(self, form):
            self.object = ContactField.create(
                self.request.org,
                self.request.user,
                name=form.cleaned_data["name"],
                value_type=form.cleaned_data["value_type"],
                featured=form.cleaned_data["show_in_table"],
                agent_access=form.cleaned_data["agent_access"],
            )
            return self.render_modal_response(form)

    class Update(FieldLookupMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        queryset = ContactField.objects.filter(is_system=False)
        form_class = ContactFieldForm
        submit_button_name = _("Update")
        success_url = "hide"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            # clear our priority if no longer featured
            if not obj.show_in_table:
                obj.priority = 0
            return obj

        def form_valid(self, form):
            super().form_valid(form)
            return self.render_modal_response(form)

    class Delete(FieldLookupMixin, DependencyDeleteModal):
        cancel_url = "@contacts.contactfield_list"
        success_url = "hide"

    class UpdatePriority(OrgPermsMixin, SmartView, View):
        def post(self, request, *args, **kwargs):
            try:
                post_data = json.loads(request.body)
                with transaction.atomic():
                    for key, priority in post_data.items():
                        ContactField.user_fields.filter(key=key, org=self.request.org).update(priority=priority)

                return HttpResponse('{"status":"OK"}', status=200, content_type="application/json")

            except Exception as e:
                logger.error(f"Could not update priorities of ContactFields: {str(e)}")

                payload = {"status": "ERROR", "err_detail": str(e)}

                return HttpResponse(json.dumps(payload), status=400, content_type="application/json")

    class List(ContentMenuMixin, SpaMixin, OrgPermsMixin, SmartListView):
        menu_path = "/contact/fields"
        title = _("Fields")
        default_order = "name"

        def build_content_menu(self, menu):
            if self.has_org_perm("contacts.contactfield_create"):
                menu.add_modax(
                    _("New Field"),
                    "new-field",
                    f"{reverse('contacts.contactfield_create')}",
                    on_submit="handleFieldUpdated()",
                    as_button=True,
                )

        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(org=self.request.org, is_active=True, is_system=False)

    class Usages(FieldLookupMixin, DependencyUsagesModal):
        permission = "contacts.contactfield_read"
        queryset = ContactField.user_fields


class ContactImportCRUDL(SmartCRUDL):
    model = ContactImport
    actions = ("create", "preview", "read")

    class Create(SpaMixin, OrgPermsMixin, SmartCreateView):
        class Form(forms.ModelForm):
            file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("xlsx",))])

            def __init__(self, *args, org, **kwargs):
                self.org = org
                self.headers = None
                self.mappings = None
                self.num_records = None

                super().__init__(*args, **kwargs)

            def clean_file(self):
                file = self.cleaned_data["file"]

                # try to parse the file saving the mappings so we don't have to repeat parsing when saving the import
                self.mappings, self.num_records = ContactImport.try_to_parse(self.org, file.file, file.name)

                return file

            class Meta:
                model = ContactImport
                fields = ("file",)

        form_class = Form
        success_url = "id@contacts.contactimport_preview"
        menu_path = "/contact/import"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.request.org
            schemes = org.get_schemes(role=Channel.ROLE_SEND)
            schemes.add(URN.TEL_SCHEME)  # always show tel
            context["urn_schemes"] = [conf for conf in URN.SCHEME_CHOICES if conf[0] in schemes]
            context["explicit_clear"] = ContactImport.EXPLICIT_CLEAR
            context["max_records"] = ContactImport.MAX_RECORDS
            context["org_country"] = org.default_country
            return context

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.org
            obj.original_filename = self.form.cleaned_data["file"].name
            obj.mappings = self.form.mappings
            obj.num_records = self.form.num_records
            return obj

    class Preview(SpaMixin, OrgObjPermsMixin, SmartUpdateView):
        menu_path = "/contact/import"

        class Form(forms.ModelForm):
            GROUP_MODE_NEW = "N"
            GROUP_MODE_EXISTING = "E"

            add_to_group = forms.BooleanField(
                label=" ", required=False, initial=True, widget=CheckboxWidget(attrs={"widget_only": True})
            )
            group_mode = forms.ChoiceField(
                required=False,
                choices=((GROUP_MODE_NEW, _("new group")), (GROUP_MODE_EXISTING, _("existing group"))),
                initial=GROUP_MODE_NEW,
                widget=SelectWidget(attrs={"widget_only": True}),
            )
            new_group_name = forms.CharField(
                label=" ", required=False, max_length=ContactGroup.MAX_NAME_LEN, widget=InputWidget()
            )
            existing_group = TembaChoiceField(
                label=" ",
                required=False,
                queryset=ContactGroup.objects.none(),
                widget=SelectWidget(
                    attrs={"placeholder": _("Select a group"), "widget_only": True, "searchable": True}
                ),
            )

            def __init__(self, *args, org, **kwargs):
                self.org = org
                super().__init__(*args, **kwargs)

                self.columns = []
                for i, item in enumerate(self.instance.mappings):
                    mapping = item["mapping"]
                    column = item.copy()

                    if mapping["type"] == "new_field":
                        include_field = forms.BooleanField(
                            label=" ", required=False, initial=True, widget=CheckboxWidget(attrs={"widget_only": True})
                        )
                        name_field = forms.CharField(
                            label=" ", initial=mapping["name"], required=False, widget=InputWidget()
                        )
                        value_type_field = forms.ChoiceField(
                            label=" ",
                            choices=ContactField.TYPE_CHOICES,
                            required=True,
                            initial=ContactField.TYPE_TEXT,
                            widget=SelectWidget(attrs={"widget_only": True}),
                        )

                        column_controls = OrderedDict(
                            [
                                (f"column_{i}_include", include_field),
                                (f"column_{i}_name", name_field),
                                (f"column_{i}_value_type", value_type_field),
                            ]
                        )
                        self.fields.update(column_controls)

                        column["controls"] = list(column_controls.keys())

                    self.columns.append(column)

                    self.fields["new_group_name"].initial = self.instance.get_default_group_name()
                    self.fields["existing_group"].queryset = ContactGroup.get_groups(org, manual_only=True).order_by(
                        "name"
                    )

            def get_form_values(self) -> list[dict]:
                """
                Gather form data into a list the same size as the mappings
                """
                data = []
                for i in range(len(self.instance.mappings)):
                    data.append(
                        {
                            "include": self.cleaned_data.get(f"column_{i}_include", True),
                            "name": self.cleaned_data.get(f"column_{i}_name", "").strip(),
                            "value_type": self.cleaned_data.get(f"column_{i}_value_type", ContactField.TYPE_TEXT),
                        }
                    )
                return data

            def clean(self):
                org_fields = self.org.fields.filter(is_system=False, is_active=True)
                existing_field_keys = {f.key for f in org_fields}
                used_field_keys = set()
                form_values = self.get_form_values()
                for data, item in zip(form_values, self.instance.mappings):
                    header, mapping = item["header"], item["mapping"]

                    if mapping["type"] == "new_field" and data["include"]:
                        field_name = data["name"]
                        if not field_name:
                            raise ValidationError(_("Field name for '%(header)s' can't be empty.") % {"header": header})
                        else:
                            field_key = ContactField.make_key(field_name)
                            if field_key in existing_field_keys:
                                raise forms.ValidationError(
                                    _("Field name for '%(header)s' matches an existing field."),
                                    params={"header": header},
                                )

                            if not ContactField.is_valid_name(field_name) or not ContactField.is_valid_key(field_key):
                                raise forms.ValidationError(
                                    _("Field name for '%(header)s' is invalid or a reserved word."),
                                    params={"header": header},
                                )

                            if field_key in used_field_keys:
                                raise forms.ValidationError(
                                    _("Field name '%(name)s' is repeated.") % {"name": field_name}
                                )

                            used_field_keys.add(field_key)

                add_to_group = self.cleaned_data["add_to_group"]
                if add_to_group:
                    group_mode = self.cleaned_data["group_mode"]
                    if group_mode == self.GROUP_MODE_NEW:
                        group_count, group_limit = ContactGroup.get_org_limit_progress(self.org)
                        if group_limit is not None and group_count >= group_limit:
                            raise forms.ValidationError(
                                _("This workspace has reached its limit of %(limit)d groups."),
                                params={"limit": group_limit},
                            )

                        new_group_name = self.cleaned_data.get("new_group_name")
                        if not new_group_name:
                            self.add_error("new_group_name", _("Required."))
                        elif not ContactGroup.is_valid_name(new_group_name):
                            self.add_error("new_group_name", _("Invalid group name."))
                        elif ContactGroup.get_group_by_name(self.org, new_group_name):
                            self.add_error("new_group_name", _("Already exists."))
                    else:
                        existing_group = self.cleaned_data.get("existing_group")
                        if not existing_group:
                            self.add_error("existing_group", _("Required."))

                return self.cleaned_data

            class Meta:
                model = ContactImport
                fields = ("id",)

        form_class = Form
        success_url = "id@contacts.contactimport_read"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def pre_process(self, request, *args, **kwargs):
            obj = self.get_object()

            # can't preview an import which has already started
            if obj.started_on:
                return HttpResponseRedirect(reverse("contacts.contactimport_read", args=[obj.id]))

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["num_records"] = self.get_object().num_records
            return context

        def pre_save(self, obj):
            form_values = self.form.get_form_values()

            # rewrite mappings using values from form
            for i, data in enumerate(form_values):
                mapping = obj.mappings[i]["mapping"]

                if not data["include"]:
                    mapping = ContactImport.MAPPING_IGNORE
                else:
                    if mapping["type"] == "new_field":
                        mapping["key"] = ContactField.make_key(data["name"])
                        mapping["name"] = data["name"]
                        mapping["value_type"] = data["value_type"]

                obj.mappings[i]["mapping"] = mapping

            if self.form.cleaned_data.get("add_to_group"):
                group_mode = self.form.cleaned_data["group_mode"]
                if group_mode == self.form.GROUP_MODE_NEW:
                    obj.group_name = self.form.cleaned_data["new_group_name"]
                    obj.group = None
                elif group_mode == self.form.GROUP_MODE_EXISTING:
                    obj.group = self.form.cleaned_data["existing_group"]

            return obj

        def post_save(self, obj):
            obj.start_async()
            return obj

    class Read(SpaMixin, OrgObjPermsMixin, NotificationTargetMixin, SmartReadView):
        menu_path = "/contact/import"
        title = _("Contact Import")

        def get_notification_scope(self) -> tuple:
            return "import:finished", f"contact:{self.object.id}"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["info"] = self.import_info
            context["is_finished"] = self.is_import_finished()
            return context

        @cached_property
        def import_info(self):
            return self.object.get_info()

        def is_import_finished(self):
            return self.import_info["status"] in (ContactImport.STATUS_COMPLETE, ContactImport.STATUS_FAILED)
