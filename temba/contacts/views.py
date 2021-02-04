import logging
from collections import OrderedDict
from datetime import timedelta
from typing import Dict, List

from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartUpdateView,
    SmartView,
    smart_url,
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
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.functional import cached_property
from django.utils.http import is_safe_url, urlquote_plus
from django.utils.translation import ugettext_lazy as _
from django.views import View

from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.templatetags.contacts import MISSING_VALUE
from temba.msgs.views import SendMessageForm
from temba.orgs.models import Org
from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.tickets.models import Ticket
from temba.utils import analytics, json, languages, on_transaction_commit
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.utils.fields import CheckboxWidget, InputWidget, SelectMultipleWidget, SelectWidget
from temba.utils.models import IDSliceQuerySet, patch_queryset_count
from temba.utils.views import BulkActionMixin, ComponentFormMixin, NonAtomicMixin

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
from .tasks import export_contacts_task, release_group_task

logger = logging.getLogger(__name__)


class RemoveFromGroupForm(forms.Form):
    contact = forms.ModelChoiceField(Contact.objects.all())
    group = forms.ModelChoiceField(ContactGroup.user_groups.all())

    def __init__(self, *args, **kwargs):
        org = kwargs.pop("org")
        self.user = kwargs.pop("user")

        super().__init__(*args, **kwargs)

        self.fields["contact"].queryset = org.contacts.filter(is_active=True)
        self.fields["group"].queryset = ContactGroup.user_groups.filter(org=org)

    def execute(self):
        data = self.cleaned_data
        contact = data["contact"]
        group = data["group"]

        assert not group.is_dynamic, "can't manually add/remove contacts for a dynamic group"

        # remove contact from group
        Contact.bulk_change_group(self.user, [contact], group, add=False)

        return {"status": "success"}


class ContactGroupForm(forms.ModelForm):
    preselected_contacts = forms.CharField(required=False, widget=forms.HiddenInput)
    group_query = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        self.org = user.get_org()
        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()

        # make sure the name isn't already taken
        existing = ContactGroup.get_user_group_by_name(self.org, name)
        if existing and self.instance != existing:
            raise forms.ValidationError(_("Name is used by another group"))

        # and that the name is valid
        if not ContactGroup.is_valid_name(name):
            raise forms.ValidationError(_("Group name must not be blank or begin with + or -"))

        org_active_group_limit = self.org.get_limit(Org.LIMIT_GROUPS)

        groups_count = ContactGroup.user_groups.filter(org=self.org).count()
        if groups_count >= org_active_group_limit:
            raise forms.ValidationError(
                _(
                    "This org has %(count)d groups and the limit is %(limit)d. "
                    "You must delete existing ones before you can "
                    "create new ones." % dict(count=groups_count, limit=org_active_group_limit)
                )
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
        fields = ("name", "query")
        model = ContactGroup


class ContactListView(OrgPermsMixin, BulkActionMixin, SmartListView):
    """
    Base class for contact list views with contact folders and groups listed by the side
    """

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

    def derive_group(self):
        return ContactGroup.all_groups.get(org=self.request.user.get_org(), group_type=self.system_group)

    def derive_export_url(self):
        search = urlquote_plus(self.request.GET.get("search", ""))
        redirect = urlquote_plus(self.request.get_full_path())
        return "%s?g=%s&s=%s&redirect=%s" % (
            reverse("contacts.contact_export"),
            self.derive_group().uuid,
            search,
            redirect,
        )

    def derive_refresh(self):
        # dynamic groups that are reevaluating should refresh every 2 seconds
        group = self.derive_group()
        if group.is_dynamic and group.status != ContactGroup.STATUS_READY:
            return 2000

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
        org = self.request.user.get_org()
        group = self.derive_group()
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
                reappearing_ids = set(group.contacts.filter(id__in=bulk_action_ids).values_list("id", flat=True))
                exclude_ids = [i for i in bulk_action_ids if i not in reappearing_ids]
            else:
                exclude_ids = []

            try:
                results = search_contacts(
                    org, search_query, group=group, sort=sort_on, offset=offset, exclude_ids=exclude_ids
                )
                self.parsed_query = results.query if len(results.query) > 0 else None
                self.save_dynamic_search = results.metadata.allow_as_group

                return IDSliceQuerySet(Contact, results.contact_ids, offset, results.total)
            except SearchException as e:
                self.search_error = str(e)

                # this should be an empty resultset
                return Contact.objects.none()
        else:
            # if user search is not defined, use DB to select contacts
            qs = (
                group.contacts.filter(org=self.request.user.get_org())
                .order_by("-id")
                .prefetch_related("org", "all_groups")
            )
            patch_queryset_count(qs, group.get_member_count)
            return qs

    def get_bulk_action_labels(self):
        return ContactGroup.get_user_groups(org=self.get_user().get_org(), dynamic=False)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        org = self.request.user.get_org()
        counts = ContactGroup.get_system_group_counts(org)

        folders = [
            dict(count=counts[ContactGroup.TYPE_ACTIVE], label=_("Active"), url=reverse("contacts.contact_list")),
            dict(count=counts[ContactGroup.TYPE_BLOCKED], label=_("Blocked"), url=reverse("contacts.contact_blocked")),
            dict(count=counts[ContactGroup.TYPE_STOPPED], label=_("Stopped"), url=reverse("contacts.contact_stopped")),
            dict(
                count=counts[ContactGroup.TYPE_ARCHIVED], label=_("Archived"), url=reverse("contacts.contact_archived")
            ),
        ]

        # resolve the paginated object list so we can initialize a cache of URNs and fields
        contacts = context["object_list"]
        Contact.bulk_cache_initialize(org, contacts)

        context["contacts"] = contacts
        context["groups"] = self.get_user_groups(org)
        context["folders"] = folders
        context["has_contacts"] = contacts or org.has_contacts()
        context["search_error"] = self.search_error
        context["send_form"] = SendMessageForm(self.request.user)
        context["folder_count"] = counts[self.system_group] if self.system_group else None

        context["sort_direction"] = self.sort_direction
        context["sort_field"] = self.sort_field

        # replace search string with parsed search expression
        if self.parsed_query is not None:
            context["search"] = self.parsed_query
            context["save_dynamic_search"] = self.save_dynamic_search

        return context

    def get_user_groups(self, org):
        groups = ContactGroup.get_user_groups(org, ready_only=False).select_related("org").order_by(Upper("name"))
        group_counts = ContactGroupCount.get_totals(groups)

        rendered = []
        for g in groups:
            rendered.append(
                {
                    "pk": g.id,
                    "uuid": g.uuid,
                    "label": g.name,
                    "count": group_counts[g],
                    "is_dynamic": g.is_dynamic,
                    "is_ready": g.status == ContactGroup.STATUS_READY,
                }
            )

        return rendered


class ContactForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs["user"]
        self.org = self.user.get_org()
        del kwargs["user"]
        super().__init__(*args, **kwargs)

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
                    required=False, label=label, initial=urn.path, help_text=help_text, widget=InputWidget(),
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
    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.filter(pk__lt=0),
        required=False,
        label=_("Groups"),
        widget=SelectMultipleWidget(attrs={"placeholder": _("Select groups for this contact")}),
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        choices = [("", "No Preference")]

        # if they had a preference that has since been removed, make sure we show it
        if self.instance.language:
            if not self.instance.org.languages.filter(iso_code=self.instance.language).first():
                lang = languages.get_language_name(self.instance.language)
                choices += [(self.instance.language, _("%s (Missing)") % lang)]

        choices += [(l.iso_code, l.name) for l in self.instance.org.languages.all().order_by("orgs", "name")]

        self.fields["language"] = forms.ChoiceField(
            required=False, label=_("Language"), initial=self.instance.language, choices=choices, widget=SelectWidget()
        )

        self.fields["groups"].initial = self.instance.user_groups.all()
        self.fields["groups"].queryset = ContactGroup.get_user_groups(self.user.get_org(), dynamic=False)
        self.fields["groups"].help_text = _("The groups which this contact belongs to")

    class Meta:
        model = Contact
        fields = ("name", "language", "groups")
        widgets = {
            "name": InputWidget(),
        }


class ExportForm(Form):
    group_memberships = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(),
        required=False,
        label=_("Group Memberships for",),
        widget=SelectMultipleWidget(
            attrs={"widget_only": True, "placeholder": _("Optional: Choose groups to show in your export")}
        ),
    )

    def __init__(self, user, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user

        self.fields["group_memberships"].queryset = ContactGroup.user_groups.filter(
            org=self.user.get_org(), is_active=True, status=ContactGroup.STATUS_READY
        ).order_by(Lower("name"))

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
        "read",
        "filter",
        "blocked",
        "omnibox",
        "update_fields",
        "update_fields_input",
        "export",
        "block",
        "restore",
        "archive",
        "delete",
        "history",
    )

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@contacts.contact_list"

        def pre_process(self, request, *args, **kwargs):
            user = self.request.user
            org = user.get_org()

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
                return HttpResponseRedirect(redirect or reverse("contacts.contact_list"))

        def derive_params(self):
            group_uuid = self.request.GET.get("g")
            search = self.request.GET.get("s")
            redirect = self.request.GET.get("redirect")
            if redirect and not is_safe_url(redirect, self.request.get_host()):
                redirect = None

            return group_uuid, search, redirect

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
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
            org = user.get_org()

            group_uuid, search, redirect = self.derive_params()
            group_memberships = form.cleaned_data["group_memberships"]

            group = ContactGroup.all_groups.filter(org=org, uuid=group_uuid).first() if group_uuid else None

            previous_export = (
                ExportContactsTask.objects.filter(org=org, created_by=user).order_by("-modified_on").first()
            )
            if previous_export and previous_export.created_on < timezone.now() - timedelta(
                hours=24
            ):  # pragma: needs cover
                analytics.track(self.request.user.username, "temba.contact_exported")

            export = ExportContactsTask.create(org, user, group, search, group_memberships)

            # schedule the export job
            on_transaction_commit(lambda: export_contacts_task.delay(export.pk))

            if not getattr(settings, "CELERY_ALWAYS_EAGER", False):  # pragma: no cover
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
                return self.render_to_response(
                    self.get_context_data(
                        form=form,
                        success_url=self.get_success_url(),
                        success_script=getattr(self, "success_script", None),
                    )
                )

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

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        fields = ("name",)

        def derive_title(self):
            return self.object.get_display()

        def get_queryset(self):
            return Contact.objects.filter(is_active=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            contact = self.object

            # the users group membership
            context["contact_groups"] = contact.user_groups.order_by(Lower("name"))

            # campaign event fires
            event_fires = contact.campaign_fires.filter(
                event__is_active=True, event__campaign__is_archived=False, scheduled__gte=timezone.now()
            ).order_by("scheduled")

            scheduled_messages = contact.get_scheduled_messages()

            merged_upcoming_events = []
            for fire in event_fires:
                merged_upcoming_events.append(
                    dict(
                        event_type=fire.event.event_type,
                        message=fire.event.get_message(contact=contact),
                        flow_uuid=fire.event.flow.uuid,
                        flow_name=fire.event.flow.name,
                        scheduled=fire.scheduled,
                    )
                )

            for sched_broadcast in scheduled_messages:
                merged_upcoming_events.append(
                    dict(
                        repeat_period=sched_broadcast.schedule.repeat_period,
                        event_type="M",
                        message=sched_broadcast.get_translated_text(contact, org=contact.org),
                        flow_uuid=None,
                        flow_name=None,
                        scheduled=sched_broadcast.schedule.next_fire,
                    )
                )

            # upcoming scheduled events
            context["upcoming_events"] = sorted(merged_upcoming_events, key=lambda k: k["scheduled"], reverse=True)

            # open tickets
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
            Contact.bulk_cache_initialize(contact.org, [contact])

            # lookup all of our contact fields
            all_contact_fields = []
            fields = ContactField.user_fields.active_for_org(org=contact.org).order_by(
                "-show_in_table", "-priority", "label", "pk"
            )

            for field in fields:
                value = contact.get_field_value(field)

                if field.show_in_table:
                    if not (value):
                        display = MISSING_VALUE
                    else:
                        display = contact.get_field_display(field)

                    all_contact_fields.append(
                        dict(id=field.id, label=field.label, value=display, show_in_table=field.show_in_table)
                    )

                else:
                    display = contact.get_field_display(field)
                    # add a contact field only if it has a value
                    if display:
                        all_contact_fields.append(
                            dict(id=field.id, label=field.label, value=display, show_in_table=field.show_in_table)
                        )

            context["all_contact_fields"] = all_contact_fields

            # add contact.language to the context
            if contact.language:
                lang = languages.get_language_name(contact.language)
                if not lang:
                    lang = contact.language
                context["contact_language"] = lang

            # calculate time after which timeline should be repeatedly refreshed - five minutes ago lets us pick up
            # status changes on new messages
            context["recent_start"] = datetime_to_ms(timezone.now() - timedelta(minutes=5))
            return context

        def post(self, request, *args, **kwargs):
            action = request.GET.get("action")

            if action == "remove_from_group":
                form = RemoveFromGroupForm(self.request.POST, org=request.user.get_org(), user=request.user)
                if form.is_valid():
                    return JsonResponse(form.execute())
                else:
                    return JsonResponse({"status": "failed"})

            return HttpResponse("unknown action", status=400)  # pragma: no cover

        def get_gear_links(self):
            links = []

            if self.has_org_perm("msgs.broadcast_send") and self.object.status == Contact.STATUS_ACTIVE:
                links.append(
                    dict(
                        id="send-message",
                        title=_("Send Message"),
                        style="button-primary",
                        href=f"{reverse('msgs.broadcast_send')}?c={self.object.uuid}",
                        modax=_("Send Message"),
                    )
                )

            if self.has_org_perm("contacts.contact_update"):

                # links.append(dict(title=_("Edit"), style="btn-primary", js_class="update-contact", href="#"))

                links.append(
                    dict(
                        id="edit-contact",
                        title=_("Edit"),
                        modax=_("Edit Contact"),
                        href=f"{reverse('contacts.contact_update', args=[self.object.pk])}",
                    )
                )

                links.append(
                    dict(
                        id="update-custom-fields",
                        title=_("Custom Fields"),
                        modax=_("Custom Fields"),
                        href=f"{reverse('contacts.contact_update_fields', args=[self.object.pk])}",
                    )
                )

                if self.object.status != Contact.STATUS_ACTIVE and self.has_org_perm("contacts.contact_restore"):
                    links.append(
                        dict(
                            title=_("Activate"),
                            style="button-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_restore", args=(self.object.pk,)),
                        )
                    )

                if self.object.status != Contact.STATUS_BLOCKED and self.has_org_perm("contacts.contact_block"):
                    links.append(
                        dict(
                            title=_("Block"),
                            style="button-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_block", args=(self.object.pk,)),
                        )
                    )

                if self.object.status != Contact.STATUS_ARCHIVED and self.has_org_perm("contacts.contact_archive"):
                    links.append(
                        dict(
                            title=_("Archive"),
                            style="btn-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_archive", args=(self.object.pk,)),
                        )
                    )

            user = self.get_user()
            if user.is_superuser or user.is_staff:
                links.append(
                    dict(
                        title=_("Service"),
                        posterize=True,
                        href=f'{reverse("orgs.org_service")}?organization={self.object.org_id}&redirect_url={reverse("contacts.contact_read", args=[self.get_object().uuid])}',
                    )
                )

            return links

    class History(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get_queryset(self):
            return Contact.objects.filter(is_active=True)

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            contact = self.get_object()

            # since we create messages with timestamps from external systems, always a chance a contact's initial
            # message has a timestamp slightly earlier than the contact itself.
            contact_creation = contact.created_on - timedelta(hours=1)

            before = int(self.request.GET.get("before", 0))
            after = int(self.request.GET.get("after", 0))

            # if we want an expanding window, or just all the recent activity
            recent_only = False
            if not before:
                recent_only = True
                before = timezone.now()
            else:
                before = ms_to_datetime(before)

            if not after:
                after = before - timedelta(days=90)
            else:
                after = ms_to_datetime(after)

            # keep looking further back until we get at least 20 items
            while True:
                history = contact.get_history(after, before)
                if recent_only or len(history) >= 20 or after == contact_creation:
                    break
                else:
                    after = max(after - timedelta(days=90), contact_creation)

            if len(history) >= Contact.MAX_HISTORY:
                after = history[-1]["created_on"]

            # check if there are more pages to fetch
            context["has_older"] = False
            if not recent_only and before > contact.created_on:
                context["has_older"] = bool(contact.get_history(contact_creation, after))

            context["recent_only"] = recent_only
            context["before"] = datetime_to_ms(after)
            context["after"] = datetime_to_ms(max(after - timedelta(days=90), contact_creation))
            context["history"] = history
            context["start_date"] = contact.org.get_delete_date(archive_type=Archive.TYPE_MSG)
            return context

    class Search(ContactListView):
        template_name = "contacts/contact_list.haml"

        def get(self, request, *args, **kwargs):
            org = self.request.user.get_org()
            query = self.request.GET.get("search", None)
            samples = int(self.request.GET.get("samples", 10))

            if not query:
                return JsonResponse({"total": 0, "sample": [], "fields": {}})

            try:
                results = search_contacts(org, query, group=org.cached_active_contacts_group, sort="-created_on")
                summary = {
                    "total": results.total,
                    "query": results.query,
                    "fields": results.metadata.fields,
                    "sample": IDSliceQuerySet(Contact, results.contact_ids, 0, results.total)[0:samples],
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
                contact_json["created_on"] = org.format_datetime(contact.created_on, False)

                json_contacts.append(contact_json)
            summary["sample"] = json_contacts

            # add in our field defs
            field_keys = [f["key"] for f in summary["fields"]]
            summary["fields"] = {
                str(f.uuid): {"label": f.label}
                for f in ContactField.user_fields.filter(org=org, key__in=field_keys, is_active=True)
            }
            return JsonResponse(summary)

    class List(ContactListView):
        title = _("Contacts")
        system_group = ContactGroup.TYPE_ACTIVE

        def get_bulk_actions(self):
            return ("label", "block", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def get_gear_links(self):
            links = []

            search = self.request.GET.get("search")

            # define save search conditions
            valid_search_condition = search and not self.search_error
            has_contactgroup_create_perm = self.has_org_perm("contacts.contactgroup_create")

            if has_contactgroup_create_perm and valid_search_condition:
                try:
                    parsed = parse_query(self.org, search)
                    if parsed.metadata.allow_as_group:
                        links.append(
                            dict(
                                id="create-smartgroup",
                                title=_("Save as Group"),
                                modax=_("Save as Group"),
                                href=f"{reverse('contacts.contactgroup_create')}?search={urlquote_plus(search)}",
                            )
                        )
                except SearchException:  # pragma: no cover
                    pass

            if self.has_org_perm("contacts.contactfield_list"):
                links.append(dict(title=_("Manage Fields"), href=reverse("contacts.contactfield_list")))

            if self.has_org_perm("contacts.contact_export"):
                links.append(
                    dict(
                        id="export-contacts",
                        title=_("Export"),
                        modax=_("Export Contacts"),
                        href=self.derive_export_url(),
                    )
                )
            return links

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.user.get_org()

            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by(
                "-show_in_table", "-priority", "pk"
            )[0:6]
            return context

    class Blocked(ContactListView):
        title = _("Blocked Contacts")
        template_name = "contacts/contact_list.haml"
        system_group = ContactGroup.TYPE_BLOCKED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Stopped(ContactListView):
        title = _("Stopped Contacts")
        template_name = "contacts/contact_stopped.haml"
        system_group = ContactGroup.TYPE_STOPPED

        def get_bulk_actions(self):
            return ("restore", "archive") if self.has_org_perm("contacts.contact_update") else ()

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Archived(ContactListView):
        title = _("Archived Contacts")
        template_name = "contacts/contact_archived.haml"
        system_group = ContactGroup.TYPE_ARCHIVED
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

        def get_gear_links(self):
            links = []
            if self.has_org_perm("contacts.contact_delete"):
                links.append(
                    dict(title=_("Delete All"), style="btn-default", js_class="contacts-btn-delete-all", href="#")
                )
            return links

    class Filter(ContactListView, OrgObjPermsMixin):
        template_name = "contacts/contact_filter.haml"

        def get_gear_links(self):
            links = []
            pk = self.derive_group().pk

            if self.has_org_perm("contacts.contactfield_list"):
                links.append(dict(title=_("Manage Fields"), href=reverse("contacts.contactfield_list")))

            if self.has_org_perm("contacts.contactgroup_update"):
                links.append(
                    dict(
                        id="edit-group",
                        title=_("Edit Group"),
                        modax=_("Edit Group"),
                        href=reverse("contacts.contactgroup_update", args=[pk]),
                    )
                )

            if self.has_org_perm("contacts.contact_export"):
                links.append(
                    dict(
                        id="export-contacts",
                        title=_("Export"),
                        modax=_("Export Contacts"),
                        href=self.derive_export_url(),
                    )
                )

            if self.has_org_perm("contacts.contactgroup_delete"):
                links.append(
                    dict(
                        id="delete-group",
                        title=_("Delete Group"),
                        modax=_("Delete Group"),
                        href=reverse("contacts.contactgroup_delete", args=[pk]),
                    )
                )
            return links

        def get_bulk_actions(self):
            return ("block", "archive") if self.derive_group().is_dynamic else ("block", "label", "unlabel")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            group = self.derive_group()
            org = self.request.user.get_org()

            context["current_group"] = group
            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by("-priority", "pk")
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<group>[^/]+)/$" % (path, action)

        def get_object_org(self):
            return ContactGroup.user_groups.get(uuid=self.kwargs["group"]).org

        def derive_group(self):
            return ContactGroup.user_groups.get(uuid=self.kwargs["group"], org=self.request.user.get_org())

    class Create(NonAtomicMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["user"] = self.request.user
            return form_kwargs

        def get_form(self):
            return super().get_form()

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def save(self, obj):
            urns = []
            for field_key, value in self.form.cleaned_data.items():
                if field_key.startswith("urn__") and value:
                    scheme = field_key.split("__")[1]
                    urns.append(URN.from_parts(scheme, value))

            Contact.create(obj.org, self.request.user, obj.name, language="", urns=urns, fields={}, groups=[])

    class Update(NonAtomicMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateContactForm
        success_url = "uuid@contacts.contact_read"
        success_message = ""
        submit_button_name = _("Save Changes")

        def derive_exclude(self):
            obj = self.get_object()
            exclude = []
            exclude.extend(self.exclude)

            if not obj.org.primary_language:
                exclude.append("language")

            if obj.status != Contact.STATUS_ACTIVE:
                exclude.append("groups")

            return exclude

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["user"] = self.request.user
            return form_kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["schemes"] = URN.SCHEME_CHOICES
            return context

        def form_valid(self, form):
            obj = self.get_object()
            data = form.cleaned_data

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

            response = self.render_to_response(
                self.get_context_data(
                    form=form,
                    success_url=self.get_success_url(),
                    success_script=getattr(self, "success_script", None),
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response

    class UpdateFields(NonAtomicMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.Form):
            contact_field = forms.ModelChoiceField(
                ContactField.user_fields.all(),
                widget=SelectWidget(
                    attrs={"widget_only": True, "searchable": True, "placeholder": _("Select a field to update")}
                ),
            )
            field_value = forms.CharField(required=False)

            def __init__(self, user, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)
                org = user.get_org()
                self.fields["contact_field"].queryset = org.contactfields(manager="user_fields").filter(is_active=True)

        form_class = Form
        success_url = "uuid@contacts.contact_read"
        success_message = ""
        submit_button_name = _("Save Changes")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.request.user.get_org()
            field_id = self.request.GET.get("field", 0)
            if field_id:
                context["contact_field"] = org.contactfields(manager="user_fields").get(id=field_id)
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

    class Block(OrgObjPermsMixin, SmartUpdateView):
        """
        Block this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = ""

        def save(self, obj):
            obj.block(self.request.user)
            return obj

    class Restore(OrgObjPermsMixin, SmartUpdateView):
        """
        Restore this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = ""

        def save(self, obj):
            obj.restore(self.request.user)
            return obj

    class Archive(OrgObjPermsMixin, SmartUpdateView):
        """
        Archive this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = ""

        def save(self, obj):
            obj.archive(self.request.user)
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
    actions = ("create", "update", "delete")

    class Create(ComponentFormMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactGroupForm
        fields = ("name", "preselected_contacts", "group_query")
        success_url = "uuid@contacts.contact_filter"
        success_message = ""
        submit_button_name = _("Create")

        def save(self, obj):
            org = self.request.user.get_org()
            user = self.request.user
            name = self.form.cleaned_data.get("name")
            query = self.form.cleaned_data.get("group_query")
            preselected_contacts = self.form.cleaned_data.get("preselected_contacts")

            if query:
                self.object = ContactGroup.create_dynamic(org, user, name, query)
            else:
                self.object = ContactGroup.create_static(org, user, name)

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
            kwargs["user"] = self.request.user
            return kwargs

    class Update(ComponentFormMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = ContactGroupForm
        fields = ("name",)
        success_url = "uuid@contacts.contact_filter"
        success_message = ""

        def derive_fields(self):
            return ("name", "query") if self.get_object().is_dynamic else ("name",)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            self.prev_query = self.get_object().query

            return super().form_valid(form)

        def post_save(self, obj):
            obj = super().post_save(obj)

            if obj.query and obj.query != self.prev_query:
                obj.update_query(obj.query)
            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        cancel_url = "uuid@contacts.contact_filter"
        redirect_url = "@contacts.contact_list"
        success_message = ""
        fields = ("uuid",)
        submit_button_name = _("Delete")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            group = self.get_object()

            context["triggers"] = group.trigger_set.filter(is_archived=False)
            context["campaigns"] = group.campaigns.filter(is_archived=False)

            return context

        def get_success_url(self):
            return reverse("contacts.contact_list")

        def post(self, request, *args, **kwargs):
            # we need a self.object for get_context_data
            self.object = self.get_object()
            group = self.object

            # if there are still dependencies, give up
            triggers = group.trigger_set.filter(is_archived=False)
            if triggers.count() > 0:
                return HttpResponseRedirect(smart_url(self.cancel_url, group))

            from temba.flows.models import Flow

            if Flow.objects.filter(org=group.org, group_dependencies__in=[group]).exists():
                return HttpResponseRedirect(smart_url(self.cancel_url, group))

            if group.campaigns.filter(is_archived=False).exists():
                return HttpResponseRedirect(smart_url(self.cancel_url, group))

            # deactivate the group, this makes it 'invisible'
            group.is_active = False
            group.save(update_fields=("is_active",))

            # release the group in a background task
            on_transaction_commit(lambda: release_group_task.delay(group.id))

            # we can't just redirect so as to make our modal do the right thing
            response = self.render_to_response(
                self.get_context_data(
                    success_url=self.get_success_url(), success_script=getattr(self, "success_script", None)
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response


class ContactFieldFormMixin:
    org = None

    def clean(self):
        cleaned_data = super().clean()
        label = cleaned_data.get("label", "")

        if not ContactField.is_valid_label(label):
            raise forms.ValidationError(_("Can only contain letters, numbers and hypens."))

        cf_exists = ContactField.user_fields.active_for_org(org=self.org).filter(label__iexact=label.lower()).exists()

        if self.instance.label != label and cf_exists is True:
            raise forms.ValidationError(_("Must be unique."))

        if not ContactField.is_valid_key(ContactField.make_key(label)):
            raise forms.ValidationError(_("Can't be a reserved word"))


class CreateContactFieldForm(ContactFieldFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()
        org_active_fields_limit = self.org.get_limit(Org.LIMIT_FIELDS)

        field_count = ContactField.user_fields.count_active_for_org(org=self.org)
        if field_count >= org_active_fields_limit:
            raise forms.ValidationError(
                _(f"Cannot create a new field as limit is %(limit)s."), params={"limit": org_active_fields_limit},
            )

    class Meta:
        model = ContactField
        fields = ("label", "value_type", "show_in_table")
        widgets = {
            "label": InputWidget(attrs={"name": _("Field Name"), "widget_only": False}),
            "value_type": SelectWidget(attrs={"widget_only": False}),
            "show_in_table": CheckboxWidget(attrs={"widget_only": True}),
        }


class UpdateContactFieldForm(ContactFieldFormMixin, forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    class Meta:
        model = ContactField
        fields = ("label", "value_type", "show_in_table")
        widgets = {
            "label": InputWidget(attrs={"name": _("Field Name"), "widget_only": False}),
            "value_type": SelectWidget(attrs={"widget_only": False}),
            "show_in_table": CheckboxWidget(attrs={"widget_only": True}),
        }


class ContactFieldListView(OrgPermsMixin, SmartListView):
    queryset = ContactField.user_fields
    title = _("Manage Contact Fields")
    fields = ("label", "show_in_table", "key", "value_type")
    search_fields = ("label__icontains", "key__icontains")
    default_order = ("label",)

    success_url = "@contacts.contactfield_list"
    link_fields = ()
    paginate_by = 10000

    template_name = "contacts/contactfield_list.haml"

    def _get_static_context_data(self, **kwargs):

        org = self.request.user.get_org()
        org_active_fields_limit = org.get_limit(Org.LIMIT_FIELDS)
        active_user_fields = self.queryset.filter(org=org, is_active=True)
        all_count = active_user_fields.count()
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
            "total_count": all_count,
            "total_limit": org_active_fields_limit,
            "cf_categories": [
                {"label": "All", "count": all_count, "url": reverse("contacts.contactfield_list")},
                {"label": "Featured", "count": featured_count, "url": reverse("contacts.contactfield_featured")},
            ],
            "cf_types": types,
        }

    def get_queryset(self, **kwargs):
        qs = super().get_queryset(**kwargs)
        qs = qs.collect_usage().filter(org=self.request.user.get_org(), is_active=True)

        return qs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        context.update(self._get_static_context_data(**kwargs))

        return context


class ContactFieldCRUDL(SmartCRUDL):
    model = ContactField
    actions = ("list", "create", "update", "update_priority", "delete", "featured", "filter_by_type", "detail")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        queryset = ContactField.user_fields
        form_class = CreateContactFieldForm
        success_message = ""
        submit_button_name = _("Create")
        field_config = {"show_in_table": {"label": _("Featured")}}

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def form_valid(self, form):
            self.object = ContactField.get_or_create(
                org=self.request.user.get_org(),
                user=self.request.user,
                key=ContactField.make_key(label=form.cleaned_data["label"]),
                label=form.cleaned_data["label"],
                value_type=form.cleaned_data["value_type"],
                show_in_table=form.cleaned_data["show_in_table"],
            )

            response = self.render_to_response(
                self.get_context_data(
                    form=form, success_url=self.get_success_url(), success_script=getattr(self, "success_script", None)
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        queryset = ContactField.user_fields
        form_class = UpdateContactFieldForm
        success_message = ""
        submit_button_name = _("Update")
        field_config = {"show_in_table": {"label": _("Featured")}}

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def form_valid(self, form):
            self.object = ContactField.get_or_create(
                org=self.request.user.get_org(),
                user=self.request.user,
                key=self.object.key,  # do not replace the key
                label=form.cleaned_data["label"],
                value_type=form.cleaned_data["value_type"],
                show_in_table=form.cleaned_data["show_in_table"],
                priority=0,  # reset the priority, this will move CF to the bottom of the list
            )

            response = self.render_to_response(
                self.get_context_data(
                    form=form, success_url=self.get_success_url(), success_script=getattr(self, "success_script", None)
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response

    class Delete(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        queryset = ContactField.user_fields
        success_url = "@contacts.contactfield_list"
        success_message = ""
        submit_button_name = _("Delete")
        http_method_names = ["get", "post"]
        fields = ("id",)

        def _has_uses(self):
            return any([self.object.flow_count, self.object.campaign_count, self.object.contactgroup_count])

        def get_queryset(self):
            qs = super().get_queryset()

            qs = qs.collect_usage()

            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["has_uses"] = self._has_uses()

            return context

        def post(self, request, *args, **kwargs):

            pk = self.kwargs.get(self.pk_url_kwarg)

            # does this ContactField actually exist
            self.object = ContactField.user_fields.filter(is_active=True, id=pk).collect_usage().get()

            # did it maybe change underneath us ???
            if self._has_uses():
                raise ValueError(f"Cannot remove a ContactField {pk}:{self.object.label} which is in use")

            else:
                self.object.hide_field(org=self.request.user.get_org(), user=self.request.user, key=self.object.key)
                response = self.render_to_response(self.get_context_data())
                response["Temba-Success"] = self.get_success_url()
                return response

    class UpdatePriority(OrgPermsMixin, SmartView, View):
        def post(self, request, *args, **kwargs):

            try:
                post_data = json.loads(request.body)

                with transaction.atomic():
                    for cfid, priority in post_data.items():
                        ContactField.user_fields.filter(id=cfid, org=self.request.user.get_org()).update(
                            priority=priority
                        )

                return HttpResponse('{"status":"OK"}', status=200, content_type="application/json")

            except Exception as e:
                logger.error(f"Could not update priorities of ContactFields: {str(e)}")

                payload = {"status": "ERROR", "err_detail": str(e)}

                return HttpResponse(json.dumps(payload), status=400, content_type="application/json")

    class List(ContactFieldListView):
        pass

    class Featured(ContactFieldListView):
        search_fields = None  # search and reordering do not work together
        default_order = ("-priority", "label")

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org(), is_active=True, show_in_table=True)

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

    class Detail(OrgObjPermsMixin, SmartReadView):
        queryset = ContactField.user_fields
        template_name = "contacts/contactfield_detail.haml"
        title = _("Contact field uses")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["dep_flows"] = list(self.object.dependent_flows.filter(is_active=True).all())
            context["dep_campaignevents"] = list(
                self.object.campaign_events.filter(is_active=True).select_related("campaign").all()
            )
            context["dep_groups"] = list(self.object.contactgroup_set.filter(is_active=True).all())

            return context


class ContactImportCRUDL(SmartCRUDL):
    model = ContactImport
    actions = ("create", "preview", "read")

    class Create(OrgPermsMixin, SmartCreateView):
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

            def clean(self):
                groups_count = ContactGroup.user_groups.filter(org=self.org).count()

                org_active_groups_limit = self.org.get_limit(Org.LIMIT_GROUPS)
                if groups_count >= org_active_groups_limit:
                    raise forms.ValidationError(
                        _(
                            "This workspace has reached the limit of %(count)d groups. "
                            "You must delete existing ones before you can perform an import."
                        ),
                        params={"count": org_active_groups_limit},
                    )

                return self.cleaned_data

            class Meta:
                model = ContactImport
                fields = ("file",)

        form_class = Form
        success_message = ""
        success_url = "id@contacts.contactimport_preview"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.derive_org()
            schemes = org.get_schemes(role=Channel.ROLE_SEND)
            schemes.add(URN.TEL_SCHEME)  # always show tel
            context["urn_schemes"] = [conf for conf in URN.SCHEME_CHOICES if conf[0] in schemes]
            context["explicit_clear"] = ContactImport.EXPLICIT_CLEAR
            context["max_records"] = ContactImport.MAX_RECORDS
            context["org_country"] = self.org.default_country
            return context

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.get_user().get_org()
            obj.original_filename = self.form.cleaned_data["file"].name
            obj.mappings = self.form.mappings
            obj.num_records = self.form.num_records
            return obj

    class Preview(OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
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
                            label=" ", initial=mapping["name"], required=False, widget=InputWidget(),
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

            def get_form_values(self) -> List[Dict]:
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
                existing_field_keys = {f.key for f in self.org.contactfields.filter(is_active=True)}
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

                            if not ContactField.is_valid_label(field_name) or not ContactField.is_valid_key(field_key):
                                raise forms.ValidationError(
                                    _("Field name for '%(header)s' is invalid or a reserved word."),
                                    params={"header": header},
                                )

                            if field_key in used_field_keys:
                                raise forms.ValidationError(
                                    _("Field name '%(name)s' is repeated.") % {"name": field_name}
                                )

                            used_field_keys.add(field_key)

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
            return obj

        def post_save(self, obj):
            obj.start_async()
            return obj

    class Read(OrgObjPermsMixin, SmartReadView):
        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["info"] = self.import_info
            context["is_finished"] = self.is_import_finished()
            return context

        @cached_property
        def import_info(self):
            return self.get_object().get_info()

        def is_import_finished(self):
            return self.import_info["status"] in (ContactImport.STATUS_COMPLETE, ContactImport.STATUS_FAILED)

        def derive_refresh(self):
            return 0 if self.is_import_finished() else 3000
