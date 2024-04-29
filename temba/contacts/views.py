import logging
from collections import OrderedDict
from datetime import timedelta
from urllib.parse import quote_plus

import iso8601
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartFormView,
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
from django.db.models import Count
from django.db.models.functions import Lower, Upper
from django.forms import Form
from django.http import Http404, HttpResponse, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.translation import gettext_lazy as _
from django.views import View

from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.templatetags.contacts import MISSING_VALUE
from temba.mailroom.events import Event
from temba.notifications.views import NotificationTargetMixin
from temba.orgs.models import User
from temba.orgs.views import (
    DependencyDeleteModal,
    DependencyUsagesModal,
    MenuMixin,
    ModalMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.tickets.models import Ticket, Ticketer, Topic
from temba.utils import analytics, json, languages, on_transaction_commit
from temba.utils.dates import datetime_to_timestamp, timestamp_to_datetime
from temba.utils.fields import (
    CheckboxWidget,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
    TembaMultipleChoiceField,
)
from temba.utils.models import patch_queryset_count
from temba.utils.models.es import IDSliceQuerySet
from temba.utils.views import BulkActionMixin, ComponentFormMixin, ContentMenuMixin, NonAtomicMixin, SpaMixin

from .models import (
    URN,
    Contact,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactImport,
    ContactURN,
    ExportContactsTask,
)
from .search import SearchException, parse_query, search_contacts
from .search.omnibox import omnibox_query, omnibox_results_to_dict
from .tasks import export_contacts_task

logger = logging.getLogger(__name__)

# events from sessions to include in contact history
HISTORY_INCLUDE_EVENTS = {
    Event.TYPE_CONTACT_LANGUAGE_CHANGED,
    Event.TYPE_CONTACT_FIELD_CHANGED,
    Event.TYPE_CONTACT_GROUPS_CHANGED,
    Event.TYPE_CONTACT_NAME_CHANGED,
    Event.TYPE_CONTACT_URNS_CHANGED,
    Event.TYPE_EMAIL_SENT,
    Event.TYPE_ERROR,
    Event.TYPE_FAILURE,
    Event.TYPE_INPUT_LABELS_ADDED,
    Event.TYPE_RUN_RESULT_CHANGED,
    Event.TYPE_WEBHOOK_CALLED,
}


class RemoveFromGroupForm(forms.Form):
    contact = TembaChoiceField(Contact.objects.none())
    group = TembaChoiceField(ContactGroup.objects.none())

    def __init__(self, *args, **kwargs):
        org = kwargs.pop("org")
        self.user = kwargs.pop("user")

        super().__init__(*args, **kwargs)

        self.fields["contact"].queryset = org.contacts.filter(is_active=True)
        self.fields["group"].queryset = ContactGroup.get_groups(org=org, manual_only=True)

    def execute(self):
        data = self.cleaned_data
        contact = data["contact"]
        group = data["group"]

        assert group.group_type == ContactGroup.TYPE_MANUAL

        # remove contact from group
        Contact.bulk_change_group(self.user, [contact], group, add=False)

        return {"status": "success"}


class ContactGroupForm(forms.ModelForm):
    preselected_contacts = forms.CharField(required=False, widget=forms.HiddenInput)
    group_query = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        existing = self.org.groups.filter(is_active=True, name__iexact=name).first()
        if existing and self.instance != existing:
            raise forms.ValidationError(_("Already used by another group."))

        count, limit = ContactGroup.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d groups. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return name

    def clean_query(self):
        try:
            parsed = parse_query(self.org, self.cleaned_data["query"])
            if not parsed.metadata.allow_as_group:
                raise forms.ValidationError(_('You cannot create a smart group based on "id" or "group".'))

            if (
                self.instance
                and self.instance.status != ContactGroup.STATUS_READY
                and parsed.query != self.instance.query
            ):
                raise forms.ValidationError(_("You cannot update the query of a group that is evaluating."))

            return parsed.query

        except SearchException as e:
            raise forms.ValidationError(str(e))

    class Meta:
        model = ContactGroup
        fields = ("name", "query")
        labels = {"name": _("Name"), "query": _("Query")}
        help_texts = {"query": _("Only contacts matching this query will belong to this group.")}


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
        redirect = quote_plus(self.request.get_full_path())
        return "%s?g=%s&s=%s&redirect=%s" % (
            reverse("contacts.contact_export"),
            self.group.uuid,
            search,
            redirect,
        )

    def derive_refresh(self):
        # smart groups that are reevaluating should refresh every 2 seconds
        if self.group.is_smart and self.group.status != ContactGroup.STATUS_READY:
            return 200000

        return None

    @staticmethod
    def prepare_sort_field_struct(sort_on):
        if not sort_on:
            return None, None, None

        if sort_on[0] == "-":
            sort_direction = "desc"
            sort_field = sort_on[1:]
        else:
            sort_direction = "asc"
            sort_field = sort_on

        if sort_field == "created_on":

            return (
                sort_field,
                sort_direction,
                {"field_type": "attribute", "sort_direction": sort_direction, "field_name": "created_on"},
            )
        if sort_field == "last_seen_on":

            return (
                sort_field,
                sort_direction,
                {"field_type": "attribute", "sort_direction": sort_direction, "field_name": "last_seen_on"},
            )
        else:
            try:
                contact_sort_field = ContactField.user_fields.values("value_type", "uuid").get(uuid=sort_field)
            except ValidationError:
                return None, None, None
            except ContactField.DoesNotExist:
                return None, None, None

            mapping = {
                "T": "text",
                "N": "number",
                "D": "datetime",
                "S": "state_keyword",
                "I": "district_keyword",
                "W": "ward_keyword",
            }
            field_leaf = mapping[contact_sort_field["value_type"]]

            return (
                sort_field,
                sort_direction,
                {
                    "field_type": "field",
                    "sort_direction": sort_direction,
                    "field_path": "fields.{}".format(field_leaf),
                    "field_uuid": str(contact_sort_field["uuid"]),
                },
            )

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
                results = search_contacts(
                    org, search_query, group=self.group, sort=sort_on, offset=offset, exclude_ids=exclude_ids
                )
                self.parsed_query = results.query if len(results.query) > 0 else None
                self.save_dynamic_search = results.metadata.allow_as_group

                return IDSliceQuerySet(Contact, results.contact_ids, offset=offset, total=results.total)
            except SearchException as e:
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

        system_groups, smart_groups, manual_groups = self.get_groups(org)

        context["contacts"] = contacts
        context["system_groups"] = system_groups
        context["smart_groups"] = smart_groups
        context["manual_groups"] = manual_groups
        context["has_contacts"] = contacts or org.get_contact_count() > 0
        context["search_error"] = self.search_error

        context["sort_direction"] = self.sort_direction
        context["sort_field"] = self.sort_field

        # replace search string with parsed search expression
        if self.parsed_query is not None:
            context["search"] = self.parsed_query
            context["save_dynamic_search"] = self.save_dynamic_search

        return context

    def get_groups(self, org) -> tuple:
        # get all groups including status groups which one day will be regular smart+system groups
        groups = org.groups.filter(is_active=True).select_related("org").order_by(Upper("name"))
        group_counts = ContactGroupCount.get_totals(groups)

        system, smart, manual = [], [], []

        for group in groups:
            obj = {
                "id": group.id,
                "name": group.name,
                "count": group_counts[group],
                "url": reverse("contacts.contact_filter", args=[group.uuid]),
            }

            if group.group_type == ContactGroup.TYPE_DB_ACTIVE:
                obj.update({"name": _("Active"), "url": reverse("contacts.contact_list")})
                system.append(obj)
            elif group.group_type == ContactGroup.TYPE_DB_BLOCKED:
                obj.update({"name": _("Blocked"), "url": reverse("contacts.contact_blocked")})
                system.append(obj)
            elif group.group_type == ContactGroup.TYPE_DB_STOPPED:
                obj.update({"name": _("Stopped"), "url": reverse("contacts.contact_stopped")})
                system.append(obj)
            elif group.group_type == ContactGroup.TYPE_DB_ARCHIVED:
                obj.update({"name": _("Archived"), "url": reverse("contacts.contact_archived")})
                system.append(obj)
            elif group.is_system:
                system.append(obj)
            elif group.group_type == ContactGroup.TYPE_SMART:
                smart.append(obj)
            else:
                manual.append(obj)

        # return system groups in the order we create them
        return sorted(system, key=lambda g: g["id"]), smart, manual


class ContactForm(forms.ModelForm):
    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

        # add all URN scheme fields if org is not anon
        extra_fields = []
        if not self.org.is_anon:
            urns = self.instance.get_urns()

            idx = 0

            last_urn = None

            if not urns:
                urn = ContactURN()
                urn.scheme = "tel"
                urns = [urn]

            for urn in urns:
                first_urn = last_urn is None or urn.scheme != last_urn.scheme

                urn_choice = None
                for choice in URN.SCHEME_CHOICES:
                    if choice[0] == urn.scheme:
                        urn_choice = choice

                scheme = urn.scheme
                label = urn.scheme

                if urn_choice:
                    label = urn_choice[1]

                help_text = _(f"{label} for this contact")
                if first_urn:
                    help_text = _(f"{label} for this contact") + f" (@urns.{scheme})"

                # get all the urns for this scheme
                ctrl = forms.CharField(
                    required=False, label=label, initial=urn.path, help_text=help_text, widget=InputWidget()
                )
                extra_fields.append(("urn__%s__%d" % (scheme, idx), ctrl))
                idx += 1

                last_urn = urn

        self.fields = OrderedDict(list(self.fields.items()) + extra_fields)

    def clean(self):
        country = self.org.default_country_code

        def validate_urn(key, scheme, path):
            try:
                normalized = URN.normalize(URN.from_parts(scheme, path), country)
                existing_urn = ContactURN.lookup(self.org, normalized, normalize=False)

                if existing_urn and existing_urn.contact and existing_urn.contact != self.instance:
                    self._errors[key] = self.error_class([_("Used by another contact")])
                    return False
                # validate but not with country as users are allowed to enter numbers before adding a channel
                elif not URN.validate(normalized):
                    if scheme == URN.TEL_SCHEME:  # pragma: needs cover
                        self._errors[key] = self.error_class(
                            [_("Invalid number. Ensure number includes country code, e.g. +1-541-754-3010")]
                        )
                    else:
                        self._errors[key] = self.error_class([_("Invalid format")])
                    return False
                return True
            except ValueError:
                self._errors[key] = self.error_class([_("Invalid input")])
                return False

        # validate URN fields
        for field_key, value in self.data.items():
            if field_key.startswith("urn__") and value:
                scheme = field_key.split("__")[1]
                validate_urn(field_key, scheme, value)

        # validate new URN if provided
        if self.data.get("new_path", None):
            if validate_urn("new_path", self.data["new_scheme"], self.data["new_path"]):
                self.cleaned_data["new_scheme"] = self.data["new_scheme"]
                self.cleaned_data["new_path"] = self.data["new_path"]

        return self.cleaned_data

    class Meta:
        model = Contact
        fields = ("name",)
        widgets = {"name": InputWidget(attrs={"widget_only": False})}


class UpdateContactForm(ContactForm):
    groups = TembaMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        required=False,
        label=_("Groups"),
        widget=SelectMultipleWidget(attrs={"placeholder": _("Select groups for this contact"), "searchable": True}),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(org, *args, **kwargs)

        choices = [("", "No Preference")]

        flow_langs = self.instance.org.flow_languages

        # if they had a preference that has since been removed, make sure we show it
        if self.instance.language and self.instance.language not in flow_langs:
            lang_name = languages.get_name(self.instance.language)
            choices += [(self.instance.language, _(f"{lang_name} (Missing)"))]

        choices += list(languages.choices(codes=flow_langs))

        self.fields["language"] = forms.ChoiceField(
            required=False, label=_("Language"), initial=self.instance.language, choices=choices, widget=SelectWidget()
        )

        self.fields["groups"].initial = self.instance.get_groups(manual_only=True)
        self.fields["groups"].queryset = ContactGroup.get_groups(self.org, manual_only=True)

    class Meta:
        model = Contact
        fields = ("name", "status", "language", "groups")
        widgets = {
            "name": InputWidget(),
        }


class ExportForm(Form):
    group_memberships = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.objects.none(),
        required=False,
        label=_("Group Memberships for"),
        widget=SelectMultipleWidget(
            attrs={"widget_only": True, "placeholder": _("Optional: Choose groups to show in your export")}
        ),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["group_memberships"].queryset = ContactGroup.get_groups(org, ready_only=True).order_by(
            Upper("name")
        )

        self.fields["group_memberships"].help_text = _(
            "Include group membership only for these groups. " "(Leave blank to ignore group memberships)."
        )


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
        "update_fields",
        "update_fields_input",
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
                    "verbose_name": _("Active Contacts"),
                    "href": reverse("contacts.contact_list"),
                    "icon": "icon.active",
                },
                {
                    "id": "archived",
                    "icon": "icon.archive",
                    "count": counts[Contact.STATUS_ARCHIVED],
                    "name": _("Archived"),
                    "verbose_name": _("Archived Contacts"),
                    "href": reverse("contacts.contact_archived"),
                },
                {
                    "id": "blocked",
                    "count": counts[Contact.STATUS_BLOCKED],
                    "name": _("Blocked"),
                    "verbose_name": _("Blocked Contacts"),
                    "href": reverse("contacts.contact_blocked"),
                    "icon": "icon.contact_blocked",
                },
                {
                    "id": "stopped",
                    "count": counts[Contact.STATUS_STOPPED],
                    "name": _("Stopped"),
                    "verbose_name": _("Stopped Contacts"),
                    "href": reverse("contacts.contact_stopped"),
                    "icon": "icon.contact_stopped",
                },
            ]

            menu.append(self.create_divider())
            menu.append(
                {
                    "id": "import",
                    "icon": "icon.upload",
                    "href": reverse("contacts.contactimport_create"),
                    "name": _("Import"),
                }
            )

            if self.has_org_perm("contacts.contactfield_list"):
                count = len(ContactField.user_fields.active_for_org(org=org))
                menu.append(
                    dict(
                        id="fields",
                        icon="icon.fields",
                        count=count,
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
                    {"id": "groups", "icon": "users", "name": _("Groups"), "items": group_items, "inline": True}
                )

            return JsonResponse({"results": menu})

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@contacts.contact_list"

        def derive_params(self):
            group_uuid = self.request.GET.get("g")
            search = self.request.GET.get("s")
            redirect = self.request.GET.get("redirect")
            if redirect and not url_has_allowed_host_and_scheme(redirect, self.request.get_host()):
                redirect = None

            return group_uuid, search, redirect

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_invalid(self, form):  # pragma: needs cover
            if "_format" in self.request.GET and self.request.GET["_format"] == "json":
                return HttpResponse(
                    json.dumps(dict(status="error", errors=form.errors)), content_type="application/json", status=400
                )
            else:
                return super().form_invalid(form)

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org
            group_uuid, search, redirect = self.derive_params()

            # is there already an export taking place?
            existing = ExportContactsTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already an export in progress, started by %s. You must wait "
                        "for that export to complete before starting another." % existing.created_by.username
                    ),
                )
            else:
                group_memberships = form.cleaned_data["group_memberships"]

                group = org.groups.filter(uuid=group_uuid).first() if group_uuid else None

                previous_export = (
                    ExportContactsTask.objects.filter(org=org, created_by=user).order_by("-modified_on").first()
                )
                if previous_export and previous_export.created_on < timezone.now() - timedelta(
                    hours=24
                ):  # pragma: needs cover
                    analytics.track(self.request.user, "temba.contact_exported")

                export = ExportContactsTask.create(org, user, group, search, group_memberships)

                # schedule the export job
                on_transaction_commit(lambda: export_contacts_task.delay(export.pk))

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: no cover
                    messages.info(
                        self.request,
                        _("We are preparing your export. We will e-mail you at %s when it is ready.")
                        % self.request.user.username,
                    )

                else:
                    dl_url = reverse("assets.download", kwargs=dict(type="contact_export", pk=export.pk))
                    messages.info(
                        self.request,
                        _("Export complete, you can find it here: %s (production users will get an email)") % dl_url,
                    )
            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(redirect or reverse("contacts.contact_list"))
            else:  # pragma: no cover
                response = self.render_to_response(
                    self.get_context_data(
                        form=form,
                        success_url=self.get_success_url(),
                        success_script=getattr(self, "success_script", None),
                    )
                )
                response["Temba-Success"] = self.get_success_url()
                return response

    class Omnibox(OrgPermsMixin, SmartListView):
        paginate_by = 75
        fields = ("id", "text")

        def get_queryset(self, **kwargs):
            org = self.derive_org()
            return omnibox_query(org, **{k: v for k, v in self.request.GET.items()})

        def render_to_response(self, context, **response_kwargs):
            org = self.derive_org()
            page = context["page_obj"]
            object_list = context["object_list"]

            results = omnibox_results_to_dict(org, object_list, self.request.GET.get("v", "1"))

            json_result = {"results": results, "more": page.has_next(), "total": len(results), "err": "nil"}

            return HttpResponse(json.dumps(json_result), content_type="application/json")

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("name",)
        select_related = ("current_flow",)

        def derive_title(self):
            return self.object.get_display()

        def get_queryset(self):
            return Contact.objects.filter(is_active=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            contact = self.object

            context["contact_groups"] = contact.get_groups().order_by(Lower("name"))
            context["upcoming_events"] = contact.get_scheduled(reverse=True)
            context["open_tickets"] = list(
                contact.tickets.filter(status=Ticket.STATUS_OPEN).select_related("ticketer").order_by("-opened_on")
            )

            # divide contact's URNs into those we can send to, and those we can't
            sendable_schemes = contact.org.get_schemes(Channel.ROLE_SEND)
            urns = contact.get_urns()
            has_sendable_urn = False

            for urn in urns:
                if urn.scheme in sendable_schemes:
                    urn.sendable = True
                    has_sendable_urn = True

            context["contact_urns"] = urns
            context["has_sendable_urn"] = has_sendable_urn

            # load our contacts values
            Contact.bulk_urn_cache_initialize([contact])

            # lookup all of our contact fields
            all_contact_fields = []
            fields = ContactField.user_fields.active_for_org(org=contact.org).order_by(
                "-show_in_table", "-priority", "name", "id"
            )

            for field in fields:
                value = contact.get_field_value(field)

                if field.show_in_table:
                    if not value:
                        display = MISSING_VALUE
                    else:
                        display = contact.get_field_display(field)

                    all_contact_fields.append(
                        dict(id=field.id, name=field.name, value=display, show_in_table=field.show_in_table)
                    )

                else:
                    display = contact.get_field_display(field)
                    # add a contact field only if it has a value
                    if display:
                        all_contact_fields.append(
                            dict(id=field.id, name=field.name, value=display, show_in_table=field.show_in_table)
                        )

            context["all_contact_fields"] = all_contact_fields

            # add contact.language to the context
            if contact.language:
                lang_name = languages.get_name(contact.language)
                context["contact_language"] = lang_name or contact.language

            # calculate time after which timeline should be repeatedly refreshed - five minutes ago lets us pick up
            # status changes on new messages
            context["recent_start"] = datetime_to_timestamp(timezone.now() - timedelta(minutes=5))
            return context

        def post(self, request, *args, **kwargs):
            action = request.GET.get("action")

            if action == "remove_from_group":
                form = RemoveFromGroupForm(self.request.POST, org=request.org, user=request.user)
                if form.is_valid():
                    return JsonResponse(form.execute())
                else:
                    return JsonResponse({"status": "failed"})

            return HttpResponse("unknown action", status=400)  # pragma: no cover

        def build_content_menu(self, menu):
            obj = self.get_object()

            if obj.status == Contact.STATUS_ACTIVE:
                if not self.is_spa() and self.has_org_perm("msgs.broadcast_send"):
                    menu.add_modax(
                        _("Send Message"),
                        "send-message",
                        f"{reverse('msgs.broadcast_send')}?c={obj.uuid}",
                        primary=True,
                        as_button=True,
                    )
                if self.has_org_perm("flows.flow_broadcast"):
                    menu.add_modax(
                        _("Start Flow"),
                        "start-flow",
                        f"{reverse('flows.flow_broadcast')}?c={obj.uuid}",
                        as_button=self.is_spa(),
                        disabled=True,
                    )
                if self.has_org_perm("contacts.contact_open_ticket"):
                    menu.add_modax(
                        _("Open Ticket"), "open-ticket", reverse("contacts.contact_open_ticket", args=[obj.id])
                    )

                menu.new_group()

                if self.has_org_perm("contacts.contact_interrupt") and obj.current_flow:
                    menu.add_url_post(_("Interrupt"), reverse("contacts.contact_interrupt", args=(obj.id,)))

            if self.has_org_perm("contacts.contact_update"):
                menu.add_modax(
                    _("Edit"),
                    "edit-contact",
                    f"{reverse('contacts.contact_update', args=[obj.id])}",
                    title=_("Edit Contact"),
                    on_submit="contactUpdated()",
                )

                if not self.is_spa():
                    menu.add_modax(
                        _("Custom Fields"),
                        "update-custom-fields",
                        f"{reverse('contacts.contact_update_fields', args=[obj.id])}",
                        on_submit="contactUpdated()",
                    )

            if self.request.user.is_staff:
                menu.new_group()
                menu.add_url_post(
                    _("Service"),
                    f'{reverse("orgs.org_service")}?organization={obj.org_id}&redirect_url={reverse("contacts.contact_read", args=[obj.uuid])}',
                )

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

        def as_json(self, context):
            return {
                "has_older": context["has_older"],
                "recent_only": context["recent_only"],
                "next_before": context["next_before"],
                "next_after": context["next_after"],
                "start_date": context["start_date"],
                "events": context["events"],
            }

    class Search(ContactListView):
        template_name = "contacts/contact_list.haml"

        def get(self, request, *args, **kwargs):
            org = self.request.org
            query = self.request.GET.get("search", None)
            samples = int(self.request.GET.get("samples", 10))

            if not query:
                return JsonResponse({"total": 0, "sample": [], "fields": {}})

            try:
                results = search_contacts(org, query, group=org.active_contacts_group, sort="-created_on")
                summary = {
                    "total": results.total,
                    "query": results.query,
                    "fields": results.metadata.fields,
                    "sample": IDSliceQuerySet(Contact, results.contact_ids, offset=0, total=results.total)[0:samples],
                }
            except SearchException as e:
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
                for f in ContactField.user_fields.filter(org=org, key__in=field_keys, is_active=True)
            }
            return JsonResponse(summary)

    class List(ContentMenuMixin, ContactListView):
        title = _("Active Contacts")
        system_group = ContactGroup.TYPE_DB_ACTIVE

        def get_bulk_actions(self):
            return ("block", "archive", "send", "start-flow") if self.has_org_perm("contacts.contact_update") else ()

        def build_content_menu(self, menu):
            is_spa = "HTTP_TEMBA_SPA" in self.request.META
            search = self.request.GET.get("search")

            # define save search conditions
            valid_search_condition = search and not self.search_error
            has_contactgroup_create_perm = self.has_org_perm("contacts.contactgroup_create")

            if has_contactgroup_create_perm and valid_search_condition:
                try:
                    parsed = parse_query(self.org, search)
                    if parsed.metadata.allow_as_group:
                        menu.add_modax(
                            _("Create Smart Group"),
                            "create-smartgroup",
                            f"{reverse('contacts.contactgroup_create')}?search={quote_plus(search)}",
                            as_button=True,
                        )
                except SearchException:  # pragma: no cover
                    pass

            if self.is_spa():
                if self.has_org_perm("contacts.contact_create"):
                    menu.add_modax(
                        _("New Contact"), "new-contact", reverse("contacts.contact_create"), title=_("New Contact")
                    )

                if has_contactgroup_create_perm:
                    menu.add_modax(
                        _("New Group"), "new-group", reverse("contacts.contactgroup_create"), title=_("New Group")
                    )

            if self.has_org_perm("contacts.contactfield_list") and not is_spa:
                menu.add_link(_("Manage Fields"), reverse("contacts.contactfield_list"), as_button=True)

            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.org

            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by(
                "-show_in_table", "-priority", "pk"
            )[0:6]
            return context

    class Blocked(ContactListView):
        title = _("Blocked Contacts")
        system_group = ContactGroup.TYPE_DB_BLOCKED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Stopped(ContactListView):
        title = _("Stopped Contacts")
        template_name = "contacts/contact_stopped.haml"
        system_group = ContactGroup.TYPE_DB_STOPPED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Archived(ContentMenuMixin, ContactListView):
        title = _("Archived Contacts")
        template_name = "contacts/contact_archived.haml"
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
            if self.has_org_perm("contacts.contact_delete"):
                menu.add_js(_("Delete All"), "handleDeleteAllContacts(event)", "contacts-btn-delete-all")

    class Filter(OrgObjPermsMixin, ContentMenuMixin, ContactListView):
        template_name = "contacts/contact_filter.haml"

        def build_content_menu(self, menu):
            is_spa = "HTTP_TEMBA_SPA" in self.request.META

            if self.has_org_perm("contacts.contactfield_list") and not is_spa:
                menu.add_link(_("Manage Fields"), reverse("contacts.contactfield_list"))

            if not self.group.is_system and self.has_org_perm("contacts.contactgroup_update"):
                menu.add_modax(_("Edit"), "edit-group", reverse("contacts.contactgroup_update", args=[self.group.id]))

            if self.has_org_perm("contacts.contact_export"):
                menu.add_modax(_("Export"), "export-contacts", self.derive_export_url(), title=_("Export Contacts"))

            menu.add_modax(
                _("Usages"), "group-usages", reverse("contacts.contactgroup_usages", args=[self.group.uuid])
            )

            if not self.group.is_system and self.has_org_perm("contacts.contactgroup_delete"):
                menu.add_modax(
                    _("Delete"), "delete-group", reverse("contacts.contactgroup_delete", args=[self.group.uuid])
                )

        def get_bulk_actions(self):
            return ("block", "archive") if self.group.is_smart else ("block", "unlabel")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.org

            context["current_group"] = self.group
            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by("-priority", "pk")
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
        form_class = ContactForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = self.request.org
            return form_kwargs

        def get_form(self):
            return super().get_form()

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.org
            return obj

        def save(self, obj):
            urns = []
            for field_key, value in self.form.cleaned_data.items():
                if field_key.startswith("urn__") and value:
                    scheme = field_key.split("__")[1]
                    urns.append(URN.from_parts(scheme, value))

            Contact.create(obj.org, self.request.user, obj.name, language="", urns=urns, fields={}, groups=[])

    class Update(SpaMixin, ComponentFormMixin, NonAtomicMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateContactForm
        success_url = "uuid@contacts.contact_read"
        success_message = ""
        submit_button_name = _("Save Changes")

        def get_success_url(self):
            if "HTTP_TEMBA_SPA" in self.request.META:
                return "hide"
            return super().get_success_url()

        def derive_exclude(self):
            obj = self.get_object()
            exclude = []
            exclude.extend(self.exclude)

            if obj.status != Contact.STATUS_ACTIVE:
                exclude.append("groups")

            return exclude

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = self.request.org
            return form_kwargs

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

            if not self.org.is_anon:
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

    class UpdateFields(NonAtomicMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.Form):
            contact_field = TembaChoiceField(
                ContactField.user_fields.none(),
                widget=SelectWidget(
                    attrs={"widget_only": True, "searchable": True, "placeholder": _("Select a field to update")}
                ),
            )

            field_value = forms.CharField(
                required=False,
                widget=InputWidget({"hide_label": True, "textarea": True}),
            )

            def __init__(self, org, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["contact_field"].queryset = org.fields.filter(is_system=False, is_active=True)

        form_class = Form
        success_url = "uuid@contacts.contact_read"
        success_message = ""
        submit_button_name = _("Save Changes")

        def get_success_url(self):
            if "HTTP_TEMBA_SPA" in self.request.META:
                return "hide"
            return super().get_success_url()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.request.org
            field_id = self.request.GET.get("field", 0)
            if field_id:
                context["contact_field"] = org.fields.get(is_system=False, id=field_id)

            return context

        def save(self, obj):
            pass

        def post_save(self, obj):
            obj = super().post_save(obj)

            field = self.form.cleaned_data.get("contact_field")
            value = self.form.cleaned_data.get("field_value", "")

            mods = obj.update_fields({field: value})
            obj.modify(self.request.user, mods)

            return obj

    class UpdateFieldsInput(OrgObjPermsMixin, SmartReadView):
        """
        Simple view for displaying a form rendered input of a contact field value. This is a helper
        view for UpdateFields to show different inputs based on the selected field.
        """

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            field_id = self.request.GET.get("field", 0)
            if field_id:
                contact_field = ContactField.user_fields.filter(id=field_id).first()
                context["contact_field"] = contact_field
                if contact_field:
                    context["value"] = self.get_object().get_field_display(contact_field)
            return context

    class OpenTicket(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Opens a new ticket for this contact.
        """

        class Form(forms.Form):
            ticketer = forms.ModelChoiceField(
                queryset=Ticketer.objects.none(), label=_("Ticket Service"), required=True
            )
            topic = forms.ModelChoiceField(queryset=Topic.objects.none(), label=_("Topic"), required=True)
            body = forms.CharField(label=_("Body"), widget=forms.Textarea, required=True)
            assignee = forms.ModelChoiceField(
                queryset=User.objects.none(),
                label=_("Assignee"),
                widget=SelectWidget(),
                required=False,
                empty_label=_("Unassigned"),
            )

            def __init__(self, instance, org, **kwargs):
                super().__init__(**kwargs)

                self.fields["ticketer"].queryset = org.ticketers.filter(is_active=True).order_by("id")
                self.fields["topic"].queryset = org.topics.filter(is_active=True).order_by("name")
                self.fields["assignee"].queryset = Ticket.get_allowed_assignees(org).order_by("email")

        form_class = Form
        submit_button_name = _("Open")
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def derive_exclude(self):
            # don't show ticketer select if they don't have external ticketers
            return ["ticketer"] if self.request.org.ticketers.filter(is_active=True).count() == 1 else []

        def save(self, obj):
            self.ticket = obj.open_ticket(
                self.request.user,
                self.form.cleaned_data.get("ticketer") or self.request.org.ticketers.filter(is_active=True).first(),
                self.form.cleaned_data["topic"],
                self.form.cleaned_data["body"],
                assignee=self.form.cleaned_data.get("assignee"),
            )

        def get_success_url(self):
            return f"{reverse('tickets.ticket_list')}all/open/{self.ticket.uuid}/"

    class Interrupt(OrgObjPermsMixin, SmartUpdateView):
        """
        Interrupt this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = ""

        def save(self, obj):
            obj.interrupt(self.request.user)
            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        """
        Delete this contact (can't be undone)
        """

        fields = ()
        success_url = "@contacts.contact_list"
        success_message = ""
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
        success_message = ""
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
        success_message = ""

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
        success_message = ""


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

        if self.instance and self.instance.campaign_events.filter(is_active=True).exists():
            if value_type != ContactField.TYPE_DATETIME:
                raise forms.ValidationError(_("Can't change type of date field being used by campaign events."))

        return value_type

    class Meta:
        model = ContactField
        fields = ("name", "value_type", "show_in_table")
        labels = {"name": _("Name"), "value_type": _("Data Type"), "show_in_table": _("Featured")}
        help_texts = {"value_type": _("The type of the values that will be stored in this field.")}
        widgets = {
            "name": InputWidget(attrs={"widget_only": False}),
            "value_type": SelectWidget(attrs={"widget_only": False}),
            "show_in_table": CheckboxWidget(attrs={"widget_only": True}),
        }


class ContactFieldListView(SpaMixin, OrgPermsMixin, SmartListView):
    queryset = ContactField.user_fields
    title = _("Fields")
    fields = ("name", "show_in_table", "key", "value_type")
    search_fields = ("name__icontains", "key__icontains")
    default_order = ("name",)

    success_url = "@contacts.contactfield_list"
    link_fields = ()
    paginate_by = 10000

    template_name = "contacts/contactfield_list.haml"

    def _get_static_context_data(self, **kwargs):
        org = self.request.org
        org_count, org_limit = ContactField.get_org_limit_progress(self.org)

        active_user_fields = self.queryset.filter(org=org, is_active=True)
        featured_count = active_user_fields.filter(show_in_table=True).count()

        type_counts = (
            active_user_fields.values("value_type")
            .annotate(type_count=Count("value_type"))
            .order_by("-type_count", "value_type")
        )
        value_type_map = {vt[0]: vt[1] for vt in ContactField.TYPE_CHOICES}
        types = [
            {
                "label": value_type_map[type_cnt["value_type"]],
                "count": type_cnt["type_count"],
                "url": reverse("contacts.contactfield_filter_by_type", args=type_cnt["value_type"]),
                "value_type": type_cnt["value_type"],
            }
            for type_cnt in type_counts
        ]

        return {
            "total_count": org_count,
            "total_limit": org_limit,
            "cf_categories": [
                {"label": "All", "count": org_count, "url": reverse("contacts.contactfield_list")},
                {"label": "Featured", "count": featured_count, "url": reverse("contacts.contactfield_featured")},
            ],
            "cf_types": types,
        }

    def get_queryset(self, **kwargs):
        qs = super().get_queryset(**kwargs)
        qs = qs.collect_usage().filter(org=self.request.org, is_active=True)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        if not self.is_spa():
            context.update(self._get_static_context_data(**kwargs))
        return context


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
            return self.request.org.fields.filter(key=self.kwargs["key"]).first()
        return None


class ContactFieldCRUDL(SmartCRUDL):
    model = ContactField
    actions = ("list", "create", "update", "update_priority", "delete", "featured", "filter_by_type", "menu", "usages")

    class Menu(OrgPermsMixin, SmartTemplateView):
        def render_to_response(self, context, **response_kwargs):

            org = self.request.org
            menu = []

            if self.has_org_perm("contacts.contactfield_list"):
                qs = ContactField.user_fields
                active_user_fields = qs.filter(org=org, is_active=True)
                featured_count = active_user_fields.filter(show_in_table=True).count()

                menu = [
                    {
                        "id": "all",
                        "name": _("All"),
                        "count": len(active_user_fields),
                        "href": reverse("contacts.contactfield_list"),
                    },
                    {
                        "icon": "bookmark",
                        "id": "featured",
                        "name": _("Featured"),
                        "count": featured_count,
                        "href": reverse("contacts.contactfield_featured"),
                    },
                ]

            return JsonResponse({"results": menu})

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
        success_message = ""
        success_url = "hide"
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context_data = super().get_context_data(**kwargs)
            org_count, org_limit = ContactField.get_org_limit_progress(self.org)
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
            )
            return self.render_modal_response(form)

    class Update(FieldLookupMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        queryset = ContactField.user_fields
        form_class = ContactFieldForm
        success_message = ""
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
        success_message = ""

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

    class List(ContentMenuMixin, ContactFieldListView):
        def build_content_menu(self, menu):
            menu.add_modax(
                _("New Field"),
                "new-field",
                f"{reverse('contacts.contactfield_create')}",
                on_submit="handleFieldUpdated()",
            )

    class Featured(ContactFieldListView):
        search_fields = None  # search and reordering do not work together
        default_order = ("-priority", "name")

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(org=self.request.org, is_active=True, show_in_table=True)

            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["is_featured_category"] = True

            return context

    class FilterByType(ContactFieldListView):
        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)

            qs = qs.filter(value_type=self.kwargs["value_type"])

            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["selected_value_type"] = self.kwargs["value_type"]

            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<value_type>[^/]+)/$" % (path, action)

    class Usages(FieldLookupMixin, DependencyUsagesModal):
        permission = "contacts.contactfield_read"
        queryset = ContactField.user_fields


class ContactImportCRUDL(SmartCRUDL):
    model = ContactImport
    actions = ("create", "preview", "read")

    class Create(SpaMixin, OrgPermsMixin, SmartCreateView):
        class Form(forms.ModelForm):
            file = forms.FileField(validators=[FileExtensionValidator(allowed_extensions=("xls", "xlsx", "csv"))])

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
        success_message = ""
        success_url = "id@contacts.contactimport_preview"

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
                            raise ValidationError(
                                _("Field name for '%(header)s' can't be empty.") % {"header": header}
                            )
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
        success_message = ""

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

    class Read(OrgObjPermsMixin, NotificationTargetMixin, SmartReadView):
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

        def derive_refresh(self):
            return 0 if self.is_import_finished() else 3000
