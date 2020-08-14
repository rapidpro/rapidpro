import logging
from collections import OrderedDict
from datetime import timedelta

import regex
from smartmin.csv_imports.models import ImportTask
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartCSVImportView,
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
from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import Count
from django.db.models.functions import Lower, Upper
from django.forms import Form
from django.http import HttpResponse, HttpResponseNotFound, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.http import is_safe_url, urlquote_plus
from django.utils.translation import ugettext_lazy as _
from django.views import View

from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.templatetags.contacts import MISSING_VALUE
from temba.msgs.views import SendMessageForm
from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.tickets.models import Ticket
from temba.utils import analytics, json, languages, on_transaction_commit
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.utils.fields import Select2Field
from temba.utils.models import IDSliceQuerySet, patch_queryset_count
from temba.utils.text import slugify_with
from temba.utils.views import BulkActionMixin, NonAtomicMixin
from temba.values.constants import Value

from .models import (
    TEL_SCHEME,
    URN,
    URN_SCHEME_CONFIG,
    Contact,
    ContactField,
    ContactGroup,
    ContactGroupCount,
    ContactURN,
    ExportContactsTask,
)
from .omnibox import omnibox_query, omnibox_results_to_dict
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

        groups_count = ContactGroup.user_groups.filter(org=self.org).count()
        if groups_count >= ContactGroup.MAX_ORG_CONTACTGROUPS:
            raise forms.ValidationError(
                _(
                    "This org has %(count)d groups and the limit is %(limit)d. "
                    "You must delete existing ones before you can "
                    "create new ones." % dict(count=groups_count, limit=ContactGroup.MAX_ORG_CONTACTGROUPS)
                )
            )

        return name

    def clean_query(self):
        from temba.contacts.search import parse_query, SearchException

        try:
            parsed = parse_query(self.org.id, self.cleaned_data["query"])
            if not parsed.metadata.allow_as_group:
                raise forms.ValidationError(_('You cannot create a dynamic group based on "id" or "group".'))

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
        from temba.contacts.search import search_contacts, SearchException

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
            try:
                results = search_contacts(org.id, str(group.uuid), search_query, sort_on, offset)
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
            dict(count=counts[ContactGroup.TYPE_ALL], label=_("All Contacts"), url=reverse("contacts.contact_list")),
            dict(count=counts[ContactGroup.TYPE_BLOCKED], label=_("Blocked"), url=reverse("contacts.contact_blocked")),
            dict(count=counts[ContactGroup.TYPE_STOPPED], label=_("Stopped"), url=reverse("contacts.contact_stopped")),
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
                for choice in ContactURN.SCHEME_CHOICES:
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
                ctrl = forms.CharField(required=False, label=label, initial=urn.path, help_text=help_text)
                extra_fields.append(("urn__%s__%d" % (scheme, idx), ctrl))
                idx += 1

                last_urn = urn

        self.fields = OrderedDict(list(self.fields.items()) + extra_fields)

    def clean(self):
        country = self.org.get_country_code()

        def validate_urn(key, scheme, path):
            try:
                normalized = URN.normalize(URN.from_parts(scheme, path), country)
                existing_urn = ContactURN.lookup(self.org, normalized, normalize=False)

                if existing_urn and existing_urn.contact and existing_urn.contact != self.instance:
                    self._errors[key] = _("Used by another contact")
                    return False
                # validate but not with country as users are allowed to enter numbers before adding a channel
                elif not URN.validate(normalized):
                    if scheme == TEL_SCHEME:  # pragma: needs cover
                        self._errors[key] = _(
                            "Invalid number. Ensure number includes country code, e.g. +1-541-754-3010"
                        )
                    else:
                        self._errors[key] = _("Invalid format")
                    return False
                return True
            except ValueError:
                self._errors[key] = _("Invalid input")
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


class UpdateContactForm(ContactForm):
    groups = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.filter(pk__lt=0),
        required=False,
        label=_("Groups"),
        help_text=_("Add or remove groups this contact belongs to"),
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
            required=False, label=_("Language"), initial=self.instance.language, choices=choices
        )

        self.fields["groups"].initial = self.instance.user_groups.all()
        self.fields["groups"].queryset = ContactGroup.get_user_groups(self.user.get_org(), dynamic=False)
        self.fields["groups"].help_text = _("The groups which this contact belongs to")

    class Meta:
        model = Contact
        fields = ("name", "language", "groups")


class ExportForm(Form):
    group_memberships = forms.ModelMultipleChoiceField(
        queryset=ContactGroup.user_groups.none(), required=False, label=_("Group Memberships for")
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
        "list",
        "import",
        "read",
        "filter",
        "blocked",
        "omnibox",
        "customize",
        "update_fields",
        "update_fields_input",
        "export",
        "block",
        "unblock",
        "unstop",
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

    class Customize(OrgPermsMixin, SmartUpdateView):
        class CustomizeForm(forms.ModelForm):
            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean(self):

                existing_contact_fields = ContactField.user_fields.active_for_org(org=self.org).values("key", "label")
                existing_contact_fields_map = {elt["label"]: elt["key"] for elt in existing_contact_fields}

                used_labels = []
                # don't allow users to specify field keys or labels
                re_col_name_field = regex.compile(r"column_\w+_label$", regex.V0)
                for key, value in self.data.items():
                    if re_col_name_field.match(key):
                        field_label = value.strip()
                        if field_label.startswith("[_NEW_]"):
                            field_label = field_label[7:]

                        field_key = ContactField.make_key(field_label)

                        if not ContactField.is_valid_label(field_label):
                            raise forms.ValidationError(_("Can only contain letters, numbers and hypens."))

                        if not ContactField.is_valid_key(field_key):
                            raise forms.ValidationError(
                                _(
                                    "%s is an invalid name or is a reserved name for contact "
                                    "fields, field names should start with a letter."
                                )
                                % value
                            )

                        if field_label in used_labels:
                            raise forms.ValidationError(_("%s should be used once") % field_label)

                        existing_key = existing_contact_fields_map.get(field_label, None)
                        if existing_key and existing_key in Contact.RESERVED_FIELD_KEYS:
                            raise forms.ValidationError(
                                _(
                                    "'%(label)s' contact field has '%(key)s' key which is reserved name. "
                                    "Column cannot be imported"
                                )
                                % dict(label=value, key=existing_key)
                            )

                        used_labels.append(field_label)

                return self.cleaned_data

            class Meta:
                model = ImportTask
                fields = "__all__"

        model = ImportTask
        form_class = CustomizeForm

        def pre_process(self, request, *args, **kwargs):
            pre_process = super().pre_process(request, *args, **kwargs)
            if pre_process is not None:  # pragma: needs cover
                return pre_process

            headers = Contact.get_org_import_file_headers(self.get_object().csv_file.file, self.derive_org())

            if not headers:
                task = self.get_object()
                self.post_save(task)
                return HttpResponseRedirect(reverse("contacts.contact_import") + "?task=%d" % task.pk)

            self.headers = headers
            return None

        def create_column_controls(self, column_headers):
            """
            Adds fields to the form for extra columns found in the spreadsheet. Returns a list of dictionaries
            containing the column label and the names of the fields
            """
            org = self.derive_org()
            column_controls = []
            for header_col in column_headers:

                header = header_col
                if header.startswith("field:"):
                    header = header.replace("field:", "", 1).strip()

                header_key = slugify_with(header)

                include_field = forms.BooleanField(label=" ", required=False, initial=True)
                include_field_name = "column_%s_include" % header_key

                label_initial = ContactField.get_by_label(org, header.title())

                label_field_initial = header.title()
                if label_initial:
                    label_field_initial = label_initial.label

                label_field = forms.CharField(initial=label_field_initial, required=False, label=" ")

                label_field_name = "column_%s_label" % header_key

                type_field_initial = None
                if label_initial:
                    type_field_initial = label_initial.value_type

                type_field = forms.ChoiceField(
                    label=" ", choices=Value.TYPE_CHOICES, required=True, initial=type_field_initial
                )
                type_field_name = "column_%s_type" % header_key

                fields = [
                    (include_field_name, include_field),
                    (label_field_name, label_field),
                    (type_field_name, type_field),
                ]

                self.form.fields = OrderedDict(list(self.form.fields.items()) + fields)

                column_controls.append(
                    dict(
                        header=header_col,
                        include_field=include_field_name,
                        label_field=label_field_name,
                        type_field=type_field_name,
                    )
                )

            return column_controls

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.derive_org()

            context["column_controls"] = self.column_controls
            context["task"] = self.get_object()

            contact_fields = sorted(
                [
                    dict(id=elt["label"], text=elt["label"])
                    for elt in ContactField.user_fields.active_for_org(org=org).values("label")
                ],
                key=lambda k: k["text"].lower(),
            )
            context["contact_fields"] = json.dumps(contact_fields)

            return context

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def get_form(self):
            form = super().get_form()
            form.fields.clear()

            self.column_controls = self.create_column_controls(self.headers)

            return form

        def pre_save(self, task):
            extra_fields = []
            cleaned_data = self.form.cleaned_data

            # enumerate the columns which the user has chosen to include as fields
            for column in self.column_controls:
                if cleaned_data[column["include_field"]]:
                    label = cleaned_data[column["label_field"]]
                    if label.startswith("[_NEW_]"):
                        label = label[7:]

                    label = label.strip()
                    value_type = cleaned_data[column["type_field"]]
                    org = self.derive_org()

                    field_key = slugify_with(label)

                    existing_field = ContactField.get_by_label(org, label)
                    if existing_field:
                        field_key = existing_field.key
                        value_type = existing_field.value_type

                    extra_fields.append(dict(key=field_key, header=column["header"], label=label, type=value_type))

            # update the extra_fields in the task's params
            params = json.loads(task.import_params)
            params["extra_fields"] = extra_fields
            task.import_params = json.dumps(params)

            return task

        def post_save(self, task):

            if not task.done():
                task.start()

            return task

        def derive_success_message(self):
            return None

        def get_success_url(self):
            return reverse("contacts.contact_import") + "?task=%d" % self.object.pk

    class Import(OrgPermsMixin, SmartCSVImportView):
        class ImportForm(forms.ModelForm):
            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean_csv_file(self):
                if not regex.match(r"^[A-Za-z0-9_.\-*() ]+$", self.cleaned_data["csv_file"].name, regex.V0):
                    raise forms.ValidationError(
                        "Please make sure the file name only contains "
                        "alphanumeric characters [0-9a-zA-Z] and "
                        "special characters in -, _, ., (, )"
                    )

                try:
                    Contact.get_org_import_file_headers(ContentFile(self.cleaned_data["csv_file"].read()), self.org)
                except Exception as e:
                    raise forms.ValidationError(str(e))

                return self.cleaned_data["csv_file"]

            def clean(self):
                groups_count = ContactGroup.user_groups.filter(org=self.org).count()
                if groups_count >= ContactGroup.MAX_ORG_CONTACTGROUPS:
                    raise forms.ValidationError(
                        _(
                            "This org has %(count)d groups and the limit is %(limit)d. "
                            "You must delete existing ones before you can "
                            "create new ones." % dict(count=groups_count, limit=ContactGroup.MAX_ORG_CONTACTGROUPS)
                        )
                    )

                return self.cleaned_data

            class Meta:
                model = ImportTask
                fields = "__all__"

        form_class = ImportForm
        model = ImportTask
        fields = ("csv_file",)
        success_message = ""

        def pre_save(self, task):
            super().pre_save(task)

            previous_import = ImportTask.objects.filter(created_by=self.request.user).order_by("-created_on").first()
            if previous_import and previous_import.created_on < timezone.now() - timedelta(
                hours=24
            ):  # pragma: needs cover
                analytics.track(self.request.user.username, "temba.contact_imported")

            return task

        def post_save(self, task):
            # configure import params with current org and timezone
            org = self.derive_org()
            params = dict(
                org_id=org.id,
                timezone=str(org.timezone),
                extra_fields=[],
                original_filename=self.form.cleaned_data["csv_file"].name,
            )
            params_dump = json.dumps(params)
            ImportTask.objects.filter(pk=task.pk).update(import_params=params_dump)

            return task

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["task"] = None
            context["group"] = None
            context["show_form"] = True
            org = self.derive_org()
            connected_channels = Channel.objects.filter(is_active=True, org=org)
            ch_schemes = set()
            for ch in connected_channels:
                ch_schemes.union(ch.schemes)

            context["urn_scheme_config"] = [
                conf for conf in URN_SCHEME_CONFIG if conf[0] == TEL_SCHEME or conf[0] in ch_schemes
            ]

            task_id = self.request.GET.get("task", None)
            if task_id:
                tasks = ImportTask.objects.filter(pk=task_id, created_by=self.request.user)

                if tasks:
                    task = tasks[0]
                    context["task"] = task
                    context["show_form"] = False
                    context["results"] = json.loads(task.import_results) if task.import_results else dict()

                    groups = ContactGroup.user_groups.filter(import_task=task)

                    if groups:
                        context["group"] = groups[0]

                    elif not task.status() in ["PENDING", "RUNNING", "STARTED"]:  # pragma: no cover
                        context["show_form"] = True

            return context

        def derive_refresh(self):
            task_id = self.request.GET.get("task", None)
            if task_id:
                tasks = ImportTask.objects.filter(pk=task_id, created_by=self.request.user)
                if tasks and tasks[0].status() in ["PENDING", "RUNNING", "STARTED"]:  # pragma: no cover
                    return 3000
                elif not ContactGroup.user_groups.filter(import_task__id=task_id).exists():
                    return 3000
            return 0

        def derive_success_message(self):
            return None

        def get_success_url(self):
            return reverse("contacts.contact_customize", args=[self.object.pk])

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

            if self.has_org_perm("msgs.broadcast_send") and not self.object.is_blocked and not self.object.is_stopped:
                links.append(
                    dict(
                        id="send-message",
                        title=_("Send Message"),
                        href=f"{reverse('msgs.broadcast_send')}?c={self.object.uuid}",
                        modax=_("Send Message"),
                    )
                )

            if self.has_org_perm("contacts.contact_update"):

                links.append(dict(title=_("Edit"), style="btn-primary", js_class="update-contact", href="#"))

                links.append(
                    dict(title=_("Custom Fields"), style="btn-primary", js_class="update-contact-fields", href="#")
                )

                if self.has_org_perm("contacts.contact_block") and not self.object.is_blocked:
                    links.append(
                        dict(
                            title=_("Block"),
                            style="btn-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_block", args=(self.object.pk,)),
                        )
                    )

                if self.has_org_perm("contacts.contact_unblock") and self.object.is_blocked:
                    links.append(
                        dict(
                            title=_("Unblock"),
                            style="btn-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_unblock", args=(self.object.pk,)),
                        )
                    )

                if self.has_org_perm("contacts.contact_unstop") and self.object.is_stopped:
                    links.append(
                        dict(
                            title=_("Unstop"),
                            style="btn-primary",
                            js_class="posterize",
                            href=reverse("contacts.contact_unstop", args=(self.object.pk,)),
                        )
                    )

                if self.has_org_perm("contacts.contact_delete"):
                    links.append(
                        dict(title=_("Delete"), style="btn-primary", js_class="contact-delete-button", href="#")
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

            from .models import MAX_HISTORY

            if len(history) >= MAX_HISTORY:
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
            from temba.contacts.search import search_contacts, SearchException

            org = self.request.user.get_org()
            query = self.request.GET.get("search", None)
            samples = int(self.request.GET.get("samples", 10))

            if not query:
                return JsonResponse({"total": 0, "sample": [], "fields": {}})

            try:
                results = search_contacts(org.id, org.cached_all_contacts_group.uuid, query, "-created_on")
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
        system_group = ContactGroup.TYPE_ALL
        bulk_actions = ("label", "block")

        def get_gear_links(self):
            links = []

            # define save search conditions
            valid_search_condition = self.request.GET.get("search") and not self.search_error
            has_contactgroup_create_perm = self.has_org_perm("contacts.contactgroup_create")

            if has_contactgroup_create_perm and valid_search_condition:
                links.append(dict(title=_("Save as Group"), js_class="add-dynamic-group", href="#"))

            if self.has_org_perm("contacts.contactfield_list"):
                links.append(
                    dict(
                        title=_("Manage Fields"), js_class="manage-fields", href=reverse("contacts.contactfield_list")
                    )
                )

            if self.has_org_perm("contacts.contact_export"):
                links.append(dict(title=_("Export"), js_class="export-contacts", href="#"))
            return links

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            org = self.request.user.get_org()

            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by("-priority", "pk")
            context["export_url"] = self.derive_export_url()
            return context

    class Blocked(ContactListView):
        title = _("Blocked Contacts")
        template_name = "contacts/contact_list.haml"
        system_group = ContactGroup.TYPE_BLOCKED

        def get_bulk_actions(self):
            return ("unblock", "delete") if self.has_org_perm("contacts.contact_delete") else ("unblock",)

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Stopped(ContactListView):
        title = _("Stopped Contacts")
        template_name = "contacts/contact_stopped.haml"
        system_group = ContactGroup.TYPE_STOPPED
        bulk_actions = ("block", "unstop")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["reply_disabled"] = True
            return context

    class Filter(ContactListView, OrgObjPermsMixin):
        template_name = "contacts/contact_filter.haml"

        def get_gear_links(self):
            links = []

            if self.has_org_perm("contacts.contactfield_list"):
                links.append(
                    dict(
                        title=_("Manage Fields"), js_class="manage-fields", href=reverse("contacts.contactfield_list")
                    )
                )

            if self.has_org_perm("contacts.contactgroup_update"):
                links.append(dict(title=_("Edit Group"), js_class="update-contactgroup", href="#"))

            if self.has_org_perm("contacts.contact_export"):
                links.append(dict(title=_("Export"), js_class="export-contacts", href="#"))

            if self.has_org_perm("contacts.contactgroup_delete"):
                links.append(dict(title=_("Delete Group"), js_class="delete-contactgroup", href="#"))
            return links

        def get_bulk_actions(self):
            return ("block", "label") if self.derive_group().is_dynamic else ("block", "label", "unlabel")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            group = self.derive_group()
            org = self.request.user.get_org()

            context["current_group"] = group
            context["contact_fields"] = ContactField.user_fields.active_for_org(org=org).order_by("-priority", "pk")
            context["export_url"] = self.derive_export_url()
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<group>[^/]+)/$" % (path, action)

        def get_object_org(self):
            return ContactGroup.user_groups.get(uuid=self.kwargs["group"]).org

        def derive_group(self):
            return ContactGroup.user_groups.get(uuid=self.kwargs["group"], org=self.request.user.get_org())

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactForm
        exclude = (
            "is_active",
            "uuid",
            "language",
            "org",
            "fields",
            "is_blocked",
            "is_stopped",
            "created_by",
            "modified_by",
            "is_test",
            "channel",
        )
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

            Contact.get_or_create_by_urns(obj.org, self.request.user, obj.name, urns)

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

            if obj.is_blocked:
                exclude.append("groups")

            return exclude

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["user"] = self.request.user
            return form_kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["schemes"] = ContactURN.SCHEME_CHOICES
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
            contact_field = Select2Field()
            field_value = forms.CharField(required=False)

            def __init__(self, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

        form_class = Form
        success_url = "uuid@contacts.contact_read"
        success_message = ""
        submit_button_name = _("Save Changes")

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

            field_id = self.form.cleaned_data.get("contact_field")
            field_obj = obj.org.contactfields(manager="user_fields").get(id=field_id)
            value = self.form.cleaned_data.get("field_value", "")

            mods = obj.update_fields({field_obj: value})
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
        success_message = _("Contact blocked")

        def save(self, obj):
            obj.block(self.request.user)
            return obj

    class Unblock(OrgObjPermsMixin, SmartUpdateView):
        """
        Unblock this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = _("Contact unblocked")

        def save(self, obj):
            obj.reactivate(self.request.user)
            return obj

    class Unstop(OrgObjPermsMixin, SmartUpdateView):
        """
        Unstops this contact
        """

        fields = ()
        success_url = "uuid@contacts.contact_read"
        success_message = _("Contact unstopped")

        def save(self, obj):
            obj.reactivate(self.request.user)
            return obj

    class Delete(OrgObjPermsMixin, SmartUpdateView):
        """
        Delete this contact (can't be undone)
        """

        fields = ()
        success_url = "@contacts.contact_list"
        success_message = ""

        def save(self, obj):
            obj.release(self.request.user)
            return obj


class ContactGroupCRUDL(SmartCRUDL):
    model = ContactGroup
    actions = ("create", "update", "delete")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
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

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
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
        fields = ("id",)

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
    class Meta:
        model = ContactField
        fields = ("label", "value_type", "show_in_table")

    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    def clean(self):
        super().clean()

        field_count = ContactField.user_fields.count_active_for_org(org=self.org)
        if field_count >= settings.MAX_ACTIVE_CONTACTFIELDS_PER_ORG:
            raise forms.ValidationError(
                _(f"Cannot create a new field as limit is %(limit)s."),
                params={"limit": settings.MAX_ACTIVE_CONTACTFIELDS_PER_ORG},
            )


class UpdateContactFieldForm(ContactFieldFormMixin, forms.ModelForm):
    class Meta:
        model = ContactField
        fields = ("label", "value_type", "show_in_table")

    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)


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

        active_user_fields = self.queryset.filter(org=self.request.user.get_org(), is_active=True)
        all_count = active_user_fields.count()
        featured_count = active_user_fields.filter(show_in_table=True).count()

        type_counts = (
            active_user_fields.values("value_type")
            .annotate(type_count=Count("value_type"))
            .order_by("-type_count", "value_type")
        )
        value_type_map = {vt[0]: vt[1] for vt in Value.TYPE_CONFIG}
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
            "total_limit": settings.MAX_ACTIVE_CONTACTFIELDS_PER_ORG,
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
    actions = ("list", "json", "create", "update", "update_priority", "delete", "featured", "filter_by_type", "detail")

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        queryset = ContactField.user_fields
        form_class = CreateContactFieldForm
        success_message = ""
        submit_button_name = _("Create")

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
        field_config = {"show_in_table": {"label": "Featured"}}

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

    class Delete(OrgObjPermsMixin, SmartUpdateView):
        queryset = ContactField.user_fields
        success_url = "@contacts.contactfield_list"
        success_message = ""
        http_method_names = ["get", "post"]

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

    class Json(OrgPermsMixin, SmartListView):
        paginate_by = None
        queryset = ContactField.user_fields

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org(), is_active=True)
            return qs

        def render_to_response(self, context, **response_kwargs):
            results = []
            for obj in context["object_list"]:
                result = dict(id=obj.pk, key=obj.key, label=obj.label)
                results.append(result)

            sorted_results = sorted(results, key=lambda k: k["label"].lower())

            sorted_results.insert(0, dict(key="groups", label="Groups"))

            for config in reversed(URN_SCHEME_CONFIG):
                sorted_results.insert(0, dict(key=config[2], label=str(config[1])))

            sorted_results.insert(0, dict(key="name", label="Full name"))

            return HttpResponse(json.dumps(sorted_results), content_type="application/json")
