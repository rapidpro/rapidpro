# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import regex
import six

from collections import OrderedDict
from datetime import timedelta
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.db import IntegrityError
from django.db.models import Q
from django.db.models.functions import Upper
from django.http import HttpResponseRedirect, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.http import urlquote_plus
from django.utils.translation import ugettext_lazy as _
from smartmin.csv_imports.models import ImportTask
from smartmin.views import SmartCreateView, SmartCRUDL, SmartCSVImportView, SmartDeleteView, SmartFormView
from smartmin.views import SmartListView, SmartReadView, SmartUpdateView, SmartTemplateView, smart_url
from temba.msgs.views import SendMessageForm
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.values.models import Value
from temba.utils import analytics, languages, on_transaction_commit
from temba.utils.dates import datetime_to_ms, ms_to_datetime
from temba.utils.fields import Select2Field
from temba.utils.text import slugify_with
from temba.utils.views import BaseActionForm
from .models import Contact, ContactGroup, ContactGroupCount, ContactField, ContactURN, URN, URN_SCHEME_CONFIG
from .models import ExportContactsTask, TEL_SCHEME
from .omnibox import omnibox_query, omnibox_results_to_dict
from .search import SearchException, parse_query
from .tasks import export_contacts_task


class RemoveContactForm(forms.Form):
    contact = forms.ModelChoiceField(Contact.objects.all())
    group = forms.ModelChoiceField(ContactGroup.user_groups.all())

    def __init__(self, *args, **kwargs):
        org = kwargs.pop('org')
        self.user = kwargs.pop('user')

        super(RemoveContactForm, self).__init__(*args, **kwargs)

        self.fields['contact'].queryset = Contact.objects.filter(org=org)
        self.fields['group'].queryset = ContactGroup.user_groups.filter(org=org)

    def clean(self):
        return self.cleaned_data

    def execute(self):
        data = self.cleaned_data
        contact = data['contact']
        group = data['group']

        if group.is_dynamic:
            raise ValueError("Can't manually add/remove contacts for a dynamic group")  # should never happen

        # remove contact from group
        group.update_contacts(self.user, [contact], False)
        return dict(group_id=group.id, contact_id=contact.id)


class ContactGroupForm(forms.ModelForm):
    preselected_contacts = forms.CharField(required=False, widget=forms.HiddenInput)
    group_query = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        self.org = user.get_org()
        super(ContactGroupForm, self).__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data['name'].strip()

        # make sure the name isn't already taken
        existing = ContactGroup.get_user_group(self.org, name)
        if existing and self.instance != existing:
            raise forms.ValidationError(_("Name is used by another group"))

        # and that the name is valid
        if not ContactGroup.is_valid_name(name):
            raise forms.ValidationError(_("Group name must not be blank or begin with + or -"))

        groups_count = ContactGroup.user_groups.filter(org=self.org).count()
        if groups_count >= ContactGroup.MAX_ORG_CONTACTGROUPS:
            raise forms.ValidationError(_("This org has %s groups and the limit is %s. "
                                          "You must delete existing ones before you can "
                                          "create new ones." % (groups_count, ContactGroup.MAX_ORG_CONTACTGROUPS)))

        return name

    def clean_query(self):
        try:
            parsed_query = parse_query(text=self.cleaned_data['query'], as_anon=self.org.is_anon)
            cleaned_query = parsed_query.as_text()

            if self.instance and self.instance.status != ContactGroup.STATUS_READY and cleaned_query != self.instance.query:
                raise forms.ValidationError(_('You cannot update the query of a group that is evaluating.'))

            if parsed_query.can_be_dynamic_group():
                return cleaned_query
            else:
                raise forms.ValidationError(
                    _('You cannot create a dynamic group based on "name" or "id".')
                )
        except SearchException as e:
            raise forms.ValidationError(six.text_type(e))

    class Meta:
        fields = '__all__'
        model = ContactGroup


class ContactListView(OrgPermsMixin, SmartListView):
    """
    Base class for contact list views with contact folders and groups listed by the side
    """
    system_group = None
    add_button = True
    paginate_by = 50

    parsed_search = None

    def derive_group(self):
        return ContactGroup.all_groups.get(org=self.request.user.get_org(), group_type=self.system_group)

    def derive_export_url(self):
        search = urlquote_plus(self.request.GET.get('search', ''))
        redirect = urlquote_plus(self.request.get_full_path())
        return '%s?g=%s&s=%s&redirect=%s' % (reverse('contacts.contact_export'), self.derive_group().uuid, search, redirect)

    def get_queryset(self, **kwargs):
        org = self.request.user.get_org()
        group = self.derive_group()
        self.search_error = None

        # contact list views don't use regular field searching but use more complex contact searching
        search_query = self.request.GET.get('search', None)
        if search_query:
            try:
                qs, self.parsed_search = Contact.search(org, search_query, group)
            except SearchException as e:
                self.search_error = six.text_type(e)
                qs = Contact.objects.none()
        else:
            qs = group.contacts.all()

        return qs.filter(is_test=False).order_by('-id').prefetch_related('org', 'all_groups')

    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = ContactGroup.get_system_group_counts(org)

        # if there isn't a search filtering the queryset, we can replace the count function with a quick cache lookup to
        # speed up paging
        if self.system_group and 'search' not in self.request.GET:
            self.object_list.count = lambda: counts[self.system_group]

        context = super(ContactListView, self).get_context_data(**kwargs)

        folders = [
            dict(count=counts[ContactGroup.TYPE_ALL], label=_("All Contacts"), url=reverse('contacts.contact_list')),
            dict(count=counts[ContactGroup.TYPE_BLOCKED], label=_("Blocked"), url=reverse('contacts.contact_blocked')),
            dict(count=counts[ContactGroup.TYPE_STOPPED], label=_("Stopped"), url=reverse('contacts.contact_stopped')),
        ]

        # resolve the paginated object list so we can initialize a cache of URNs and fields
        contacts = list(context['object_list'])
        Contact.bulk_cache_initialize(org, contacts, for_show_only=True)

        context['contacts'] = contacts
        context['groups'] = self.get_user_groups(org)
        context['folders'] = folders
        context['has_contacts'] = contacts or org.has_contacts()
        context['search_error'] = self.search_error
        context['send_form'] = SendMessageForm(self.request.user)

        # replace search string with parsed search expression
        if self.parsed_search is not None:
            context['search'] = self.parsed_search.as_text()
            context['save_dynamic_search'] = self.parsed_search.can_be_dynamic_group()

        return context

    def get_user_groups(self, org):
        groups = (
            ContactGroup.get_user_groups(org, ready_only=False)
            .select_related('org')
            .order_by(Upper('name'))
        )
        group_counts = ContactGroupCount.get_totals(groups)

        rendered = []
        for g in groups:
            rendered.append({
                'pk': g.id,
                'uuid': g.uuid,
                'label': g.name,
                'count': group_counts[g],
                'is_dynamic': g.is_dynamic,
                'is_ready': g.status == ContactGroup.STATUS_READY
            })

        return rendered


class ContactActionForm(BaseActionForm):
    allowed_actions = (('label', _("Add to Group")),
                       ('unlabel', _("Remove from Group")),
                       ('unblock', _("Unblock Contacts")),
                       ('block', _("Block Contacts")),
                       ('delete', _("Delete Contacts")),
                       ('unstop', _("Unstop Contacts")))

    model = Contact
    label_model = ContactGroup
    label_model_manager = 'user_groups'
    has_is_active = True

    class Meta:
        fields = ('action', 'label', 'objects', 'add')


class ContactActionMixin(SmartListView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(ContactActionMixin, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        org = user.get_org()

        form = ContactActionForm(self.request.POST, org=org, user=user)

        if form.is_valid():
            form.execute()

        return self.get(request, *args, **kwargs)


class ContactFieldForm(forms.ModelForm):

    contact_field = Select2Field()
    field_value = forms.CharField(required=False)

    def __init__(self, *args, **kwargs):
        super(ContactFieldForm, self).__init__(*args, **kwargs)

    class Meta:
        model = Contact
        fields = '__all__'


class ContactForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs['user']
        self.org = self.user.get_org()
        del kwargs['user']
        super(ContactForm, self).__init__(*args, **kwargs)

        # add all URN scheme fields if org is not anon
        extra_fields = []
        if not self.org.is_anon:
            urns = self.instance.get_urns()

            idx = 0

            last_urn = None

            if not urns:
                urn = ContactURN()
                urn.scheme = 'tel'
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

                help_text = '%s for this contact' % label
                if first_urn:
                    help_text = '%s for this contact (@contact.%s)' % (label, scheme)

                # get all the urns for this scheme
                ctrl = forms.CharField(required=False, label=label, initial=urn.path, help_text=help_text)
                extra_fields.append(('urn__%s__%d' % (scheme, idx), ctrl))
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
                        self._errors[key] = _("Invalid number. Ensure number includes country code, e.g. +1-541-754-3010")
                    else:
                        self._errors[key] = _("Invalid format")
                    return False
                return True
            except ValueError:
                self._errors[key] = _("Invalid input")
                return False

        # validate URN fields
        for field_key, value in six.iteritems(self.data):
            if field_key.startswith('urn__') and value:
                scheme = field_key.split('__')[1]
                validate_urn(field_key, scheme, value)

        # validate new URN if provided
        if self.data.get('new_path', None):
            if validate_urn('new_path', self.data['new_scheme'], self.data['new_path']):
                self.cleaned_data['new_scheme'] = self.data['new_scheme']
                self.cleaned_data['new_path'] = self.data['new_path']

        return self.cleaned_data

    class Meta:
        model = Contact
        fields = '__all__'


class UpdateContactForm(ContactForm):
    groups = forms.ModelMultipleChoiceField(queryset=ContactGroup.user_groups.filter(pk__lt=0),
                                            required=False, label=_("Groups"),
                                            help_text=_("Add or remove groups this contact belongs to"))

    def __init__(self, *args, **kwargs):
        super(UpdateContactForm, self).__init__(*args, **kwargs)

        choices = [('', 'No Preference')]

        # if they had a preference that has since been removed, make sure we show it
        if self.instance.language:
            if not self.instance.org.languages.filter(iso_code=self.instance.language).first():
                lang = languages.get_language_name(self.instance.language)
                choices += [(self.instance.language, _("%s (Missing)") % lang)]

        choices += [(l.iso_code, l.name) for l in self.instance.org.languages.all().order_by('orgs', 'name')]

        self.fields['language'] = forms.ChoiceField(required=False, label=_('Language'),
                                                    initial=self.instance.language, choices=choices)

        self.fields['groups'].initial = self.instance.user_groups.all()
        self.fields['groups'].queryset = ContactGroup.get_user_groups(self.user.get_org(), dynamic=False)
        self.fields['groups'].help_text = _("The groups which this contact belongs to")


class ContactCRUDL(SmartCRUDL):
    model = Contact
    actions = ('create', 'update', 'stopped', 'list', 'import', 'read', 'filter', 'blocked', 'omnibox',
               'customize', 'update_fields', 'update_fields_input', 'export', 'block', 'unblock', 'unstop', 'delete', 'history')

    class Export(OrgPermsMixin, SmartTemplateView):
        def render_to_response(self, context, **response_kwargs):
            user = self.request.user
            org = user.get_org()

            group_uuid = self.request.GET.get('g')
            search = self.request.GET.get('s')
            redirect = self.request.GET.get('redirect')

            group = ContactGroup.all_groups.filter(org=org, uuid=group_uuid).first() if group_uuid else None

            # is there already an export taking place?
            existing = ExportContactsTask.get_recent_unfinished(org)
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))

            # otherwise, off we go
            else:
                previous_export = ExportContactsTask.objects.filter(org=org, created_by=user).order_by('-modified_on').first()
                if previous_export and previous_export.created_on < timezone.now() - timedelta(hours=24):  # pragma: needs cover
                    analytics.track(self.request.user.username, 'temba.contact_exported')

                export = ExportContactsTask.create(org, user, group, search)

                # schedule the export job
                on_transaction_commit(lambda: export_contacts_task.delay(export.pk))

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):  # pragma: no cover
                    messages.info(self.request,
                                  _("We are preparing your export. We will e-mail you at %s when it is ready.")
                                  % self.request.user.username)

                else:
                    dl_url = reverse('assets.download', kwargs=dict(type='contact_export', pk=export.pk))
                    messages.info(self.request,
                                  _("Export complete, you can find it here: %s (production users will get an email)")
                                  % dl_url)

            return HttpResponseRedirect(redirect or reverse('contacts.contact_list'))

    class Customize(OrgPermsMixin, SmartUpdateView):

        class CustomizeForm(forms.ModelForm):
            def __init__(self, *args, **kwargs):
                self.org = kwargs['org']
                del kwargs['org']
                super(ContactCRUDL.Customize.CustomizeForm, self).__init__(*args, **kwargs)

            def clean(self):

                existing_contact_fields = ContactField.objects.filter(org=self.org, is_active=True).values('key', 'label')
                existing_contact_fields_map = {elt['label']: elt['key'] for elt in existing_contact_fields}

                used_labels = []
                # don't allow users to specify field keys or labels
                re_col_name_field = regex.compile(r'column_\w+_label', regex.V0)
                for key, value in self.data.items():
                    if re_col_name_field.match(key):
                        field_label = value.strip()
                        if field_label.startswith('[_NEW_]'):
                            field_label = field_label[7:]

                        field_key = ContactField.make_key(field_label)

                        if not ContactField.is_valid_label(field_label):
                            raise forms.ValidationError(_("Field names can only contain letters, numbers, hypens"))

                        if not ContactField.is_valid_key(field_key):
                            raise forms.ValidationError(_("%s is an invalid name or is a reserved name for contact "
                                                          "fields, field names should start with a letter.") % value)

                        if field_label in used_labels:
                            raise forms.ValidationError(_("%s should be used once") % field_label)

                        existing_key = existing_contact_fields_map.get(field_label, None)
                        if existing_key and existing_key in Contact.RESERVED_FIELD_KEYS:
                            raise forms.ValidationError(_("'%s' contact field has '%s' key which is reserved name. "
                                                          "Column cannot be imported") % (value, existing_key))

                        used_labels.append(field_label)

                return self.cleaned_data

            class Meta:
                model = ImportTask
                fields = '__all__'

        model = ImportTask
        form_class = CustomizeForm

        def pre_process(self, request, *args, **kwargs):
            pre_process = super(ContactCRUDL.Customize, self).pre_process(request, *args, **kwargs)
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
            for header in column_headers:
                header_key = slugify_with(header)

                include_field = forms.BooleanField(label=' ', required=False, initial=True)
                include_field_name = 'column_%s_include' % header_key

                label_initial = ContactField.get_by_label(org, header.title())

                label_field_initial = header.title()
                if label_initial:
                    label_field_initial = label_initial.label

                label_field = forms.CharField(initial=label_field_initial, required=False, label=' ')

                label_field_name = 'column_%s_label' % header_key

                type_field_initial = None
                if label_initial:
                    type_field_initial = label_initial.value_type

                type_field = forms.ChoiceField(label=' ', choices=Value.TYPE_CHOICES, required=True,
                                               initial=type_field_initial)
                type_field_name = 'column_%s_type' % header_key

                fields = [
                    (include_field_name, include_field),
                    (label_field_name, label_field),
                    (type_field_name, type_field)
                ]

                self.form.fields = OrderedDict(list(self.form.fields.items()) + fields)

                column_controls.append(dict(header=header,
                                            include_field=include_field_name,
                                            label_field=label_field_name,
                                            type_field=type_field_name))

            return column_controls

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Customize, self).get_context_data(**kwargs)

            org = self.derive_org()

            context['column_controls'] = self.column_controls
            context['task'] = self.get_object()

            contact_fields = sorted([dict(id=elt['label'], text=elt['label']) for elt in ContactField.objects.filter(org=org, is_active=True).values('label')], key=lambda k: k['text'].lower())
            context['contact_fields'] = json.dumps(contact_fields)

            return context

        def get_form_kwargs(self):
            kwargs = super(ContactCRUDL.Customize, self).get_form_kwargs()
            kwargs['org'] = self.derive_org()
            return kwargs

        def get_form(self):
            form = super(ContactCRUDL.Customize, self).get_form()
            form.fields.clear()

            self.column_controls = self.create_column_controls(self.headers)

            return form

        def pre_save(self, task):
            extra_fields = []
            cleaned_data = self.form.cleaned_data

            # enumerate the columns which the user has chosen to include as fields
            for column in self.column_controls:
                if cleaned_data[column['include_field']]:
                    label = cleaned_data[column['label_field']]
                    if label.startswith("[_NEW_]"):
                        label = label[7:]

                    label = label.strip()
                    value_type = cleaned_data[column['type_field']]
                    org = self.derive_org()

                    field_key = slugify_with(label)

                    existing_field = ContactField.get_by_label(org, label)
                    if existing_field:
                        field_key = existing_field.key
                        value_type = existing_field.value_type

                    extra_fields.append(dict(key=field_key, header=column['header'], label=label, type=value_type))

            # update the extra_fields in the task's params
            params = json.loads(task.import_params)
            params['extra_fields'] = extra_fields
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
                self.org = kwargs['org']
                del kwargs['org']
                super(ContactCRUDL.Import.ImportForm, self).__init__(*args, **kwargs)

            def clean_csv_file(self):
                if not regex.match(r'^[A-Za-z0-9_.\-*() ]+$', self.cleaned_data['csv_file'].name, regex.V0):
                    raise forms.ValidationError('Please make sure the file name only contains '
                                                'alphanumeric characters [0-9a-zA-Z] and '
                                                'special characters in -, _, ., (, )')

                try:
                    Contact.get_org_import_file_headers(ContentFile(self.cleaned_data['csv_file'].read()), self.org)
                except Exception as e:
                    raise forms.ValidationError(str(e))

                return self.cleaned_data['csv_file']

            def clean(self):
                groups_count = ContactGroup.user_groups.filter(org=self.org).count()
                if groups_count >= ContactGroup.MAX_ORG_CONTACTGROUPS:
                    raise forms.ValidationError(_("This org has %s groups and the limit is %s. "
                                                  "You must delete existing ones before you can "
                                                  "create new ones." % (groups_count,
                                                                        ContactGroup.MAX_ORG_CONTACTGROUPS)))

                return self.cleaned_data

            class Meta:
                model = ImportTask
                fields = '__all__'

        form_class = ImportForm
        model = ImportTask
        fields = ('csv_file',)
        success_message = ''

        def pre_save(self, task):
            super(ContactCRUDL.Import, self).pre_save(task)

            previous_import = ImportTask.objects.filter(created_by=self.request.user).order_by('-created_on').first()
            if previous_import and previous_import.created_on < timezone.now() - timedelta(hours=24):  # pragma: needs cover
                analytics.track(self.request.user.username, 'temba.contact_imported')

            return task

        def post_save(self, task):
            # configure import params with current org and timezone
            org = self.derive_org()
            params = dict(org_id=org.id, timezone=six.text_type(org.timezone), extra_fields=[], original_filename=self.form.cleaned_data['csv_file'].name)
            params_dump = json.dumps(params)
            ImportTask.objects.filter(pk=task.pk).update(import_params=params_dump)

            return task

        def get_form_kwargs(self):
            kwargs = super(ContactCRUDL.Import, self).get_form_kwargs()
            kwargs['org'] = self.derive_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Import, self).get_context_data(**kwargs)
            context['task'] = None
            context['group'] = None
            context['show_form'] = True

            task_id = self.request.GET.get('task', None)
            if task_id:
                tasks = ImportTask.objects.filter(pk=task_id, created_by=self.request.user)

                if tasks:
                    task = tasks[0]
                    context['task'] = task
                    context['show_form'] = False
                    context['results'] = json.loads(task.import_results) if task.import_results else dict()

                    groups = ContactGroup.user_groups.filter(import_task=task)

                    if groups:
                        context['group'] = groups[0]

                    elif not task.status() in ['PENDING', 'RUNNING', 'STARTED']:  # pragma: no cover
                        context['show_form'] = True

            return context

        def derive_refresh(self):
            task_id = self.request.GET.get('task', None)
            if task_id:
                tasks = ImportTask.objects.filter(pk=task_id, created_by=self.request.user)
                if tasks and tasks[0].status() in ['PENDING', 'RUNNING', 'STARTED']:  # pragma: no cover
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
        fields = ('id', 'text')

        def get_queryset(self, **kwargs):
            org = self.derive_org()

            return omnibox_query(org, **{k: v for k, v in self.request.GET.items()})

        def render_to_response(self, context, **response_kwargs):
            org = self.derive_org()
            page = context['page_obj']
            object_list = context['object_list']

            results = omnibox_results_to_dict(org, object_list)

            json_result = {'results': results, 'more': page.has_next(), 'total': len(results), 'err': 'nil'}

            return HttpResponse(json.dumps(json_result), content_type='application/json')

    class Read(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = 'uuid'
        fields = ('name',)

        def derive_title(self):
            return self.object.get_display()

        def get_queryset(self):
            return Contact.objects.filter(is_active=True, is_test=False)

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Read, self).get_context_data(**kwargs)

            contact = self.object

            # the users group membership
            context['contact_groups'] = contact.user_groups.extra(select={'lower_name': 'lower(name)'}).order_by('lower_name')

            # event fires
            event_fires = contact.fire_events.filter(scheduled__gte=timezone.now()).order_by('scheduled')
            scheduled_messages = contact.get_scheduled_messages()

            merged_upcoming_events = []
            for fire in event_fires:
                merged_upcoming_events.append(dict(event_type=fire.event.event_type,
                                                   message=fire.event.get_message(contact=contact),
                                                   flow_uuid=fire.event.flow.uuid, flow_name=fire.event.flow.name,
                                                   scheduled=fire.scheduled))

            for sched_broadcast in scheduled_messages:
                merged_upcoming_events.append(dict(repeat_period=sched_broadcast.schedule.repeat_period, event_type='M',
                                                   message=sched_broadcast.get_translated_text(contact, org=contact.org),
                                                   flow_uuid=None, flow_name=None,
                                                   scheduled=sched_broadcast.schedule.next_fire))

            # upcoming scheduled events
            context['upcoming_events'] = sorted(merged_upcoming_events, key=lambda k: k['scheduled'], reverse=True)

            # divide contact's URNs into those we can send to, and those we can't
            from temba.channels.models import Channel
            sendable_schemes = contact.org.get_schemes(Channel.ROLE_SEND)

            urns = contact.get_urns()
            has_sendable_urn = False

            for urn in urns:
                if urn.scheme in sendable_schemes:
                    urn.sendable = True
                    has_sendable_urn = True

            context['contact_urns'] = urns
            context['has_sendable_urn'] = has_sendable_urn

            # load our contacts values
            Contact.bulk_cache_initialize(contact.org, [contact])

            # lookup all of our contact fields
            contact_fields = []
            fields = ContactField.objects.filter(org=contact.org, is_active=True).order_by('label', 'pk')
            for field in fields:
                value = getattr(contact, '__field__%s' % field.key)
                if value:
                    display = Contact.get_field_display_for_value(field, value)
                    contact_fields.append(dict(id=field.id, label=field.label, value=display, featured=field.show_in_table))

            # stuff in the contact's language in the fields as well
            if contact.language:
                lang = languages.get_language_name(contact.language)
                if not lang:
                    lang = contact.language
                contact_fields.append(dict(label='Language', value=lang, featured=True))

            context['contact_fields'] = sorted(contact_fields, key=lambda f: f['label'])

            # calculate time after which timeline should be repeatedly refreshed - five minutes ago lets us pick up
            # status changes on new messages
            context['recent_start'] = datetime_to_ms(timezone.now() - timedelta(minutes=5))
            return context

        def post(self, request, *args, **kwargs):
            form = RemoveContactForm(self.request.POST, org=request.user.get_org(), user=request.user)
            if form.is_valid():
                result = form.execute()
                return HttpResponse(json.dumps(result))

            # shouldn't ever happen
            else:  # pragma: no cover
                raise forms.ValidationError(_("Invalid group or contact id"))

        def get_gear_links(self):
            links = []

            if self.has_org_perm("msgs.broadcast_send") and not self.object.is_blocked and not self.object.is_stopped:
                links.append(dict(title=_('Send Message'),
                                  style='btn-primary',
                                  href='#',
                                  js_class='contact-send-button'))

            if self.has_org_perm("contacts.contact_update"):

                links.append(dict(title=_('Edit'), style='btn-primary', js_class='update-contact', href="#"))

                links.append(dict(title=_('Custom Fields'), style='btn-primary', js_class='update-contact-fields', href="#"))

                if self.has_org_perm("contacts.contact_block") and not self.object.is_blocked:
                    links.append(dict(title=_('Block'), style='btn-primary', js_class='posterize',
                                      href=reverse('contacts.contact_block', args=(self.object.pk,))))

                if self.has_org_perm("contacts.contact_unblock") and self.object.is_blocked:
                    links.append(dict(title=_('Unblock'), style='btn-primary', js_class='posterize',
                                      href=reverse('contacts.contact_unblock', args=(self.object.pk,))))

                if self.has_org_perm("contacts.contact_unstop") and self.object.is_stopped:
                    links.append(dict(title=_('Unstop'), style='btn-primary', js_class='posterize',
                                      href=reverse('contacts.contact_unstop', args=(self.object.pk,))))

                if self.has_org_perm("contacts.contact_delete"):
                    links.append(dict(title=_('Delete'), style='btn-primary',
                                      js_class='contact-delete-button', href='#'))

            return links

    class History(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = 'uuid'

        def get_queryset(self):
            return Contact.objects.filter(is_active=True, is_test=False)

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.History, self).get_context_data(*args, **kwargs)
            contact = self.get_object()

            # since we create messages with timestamps from external systems, always a chance a contact's initial
            # message has a timestamp slightly earlier than the contact itself.
            contact_creation = contact.created_on - timedelta(hours=1)

            before = int(self.request.GET.get('before', 0))
            after = int(self.request.GET.get('after', 0))

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
                activity = contact.get_activity(after, before)
                if recent_only or len(activity) >= 20 or after == contact_creation:
                    break
                else:
                    after = max(after - timedelta(days=90), contact_creation)

            # mark our after as the last item in our list
            from temba.contacts.models import MAX_HISTORY
            if len(activity) >= MAX_HISTORY:
                after = activity[-1]['time']

            # check if there are more pages to fetch
            context['has_older'] = False
            if not recent_only and before > contact.created_on:
                context['has_older'] = bool(contact.get_activity(contact_creation, after))

            context['recent_only'] = recent_only
            context['before'] = datetime_to_ms(after)
            context['after'] = datetime_to_ms(max(after - timedelta(days=90), contact_creation))
            context['activity'] = activity
            return context

    class List(ContactActionMixin, ContactListView):
        title = _("Contacts")
        system_group = ContactGroup.TYPE_ALL

        def get_gear_links(self):
            links = []

            # define save search conditions
            valid_search_condition = self.request.GET.get('search') and not self.search_error
            has_contactgroup_create_perm = self.has_org_perm('contacts.contactgroup_create')

            if has_contactgroup_create_perm and valid_search_condition:
                links.append(dict(title=_('Save as Group'), js_class='add-dynamic-group', href="#"))

            if self.has_org_perm('contacts.contactfield_managefields'):
                links.append(dict(title=_('Manage Fields'), js_class='manage-fields', href="#"))

            if self.has_org_perm('contacts.contact_export'):
                links.append(dict(title=_('Export'), href=self.derive_export_url()))
            return links

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.List, self).get_context_data(*args, **kwargs)
            org = self.request.user.get_org()

            context['actions'] = ('label', 'block')
            context['contact_fields'] = ContactField.objects.filter(org=org, is_active=True).order_by('pk')
            return context

    class Blocked(ContactActionMixin, ContactListView):
        title = _("Blocked Contacts")
        template_name = 'contacts/contact_list.haml'
        system_group = ContactGroup.TYPE_BLOCKED

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.Blocked, self).get_context_data(*args, **kwargs)
            context['actions'] = ('unblock', 'delete') if self.has_org_perm("contacts.contact_delete") else ('unblock',)
            context['reply_disabled'] = True
            return context

    class Stopped(ContactActionMixin, ContactListView):
        title = _("Stopped Contacts")
        template_name = 'contacts/contact_stopped.haml'
        system_group = ContactGroup.TYPE_STOPPED

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.Stopped, self).get_context_data(*args, **kwargs)
            context['actions'] = ['block', 'unstop']
            context['reply_disabled'] = True
            return context

    class Filter(ContactActionMixin, ContactListView):
        template_name = "contacts/contact_filter.haml"

        def get_gear_links(self):
            links = []

            if self.has_org_perm('contacts.contactfield_managefields'):
                links.append(dict(title=_('Manage Fields'),
                                  js_class='manage-fields',
                                  href="#"))

            if self.has_org_perm('contacts.contactgroup_update'):
                links.append(dict(title=_('Edit Group'),
                                  js_class='update-contactgroup',
                                  href="#"))

            if self.has_org_perm('contacts.contact_export'):
                links.append(dict(title=_('Export'), href=self.derive_export_url()))

            if self.has_org_perm('contacts.contactgroup_delete'):
                links.append(dict(title=_('Delete Group'),
                                  js_class='delete-contactgroup',
                                  href="#"))
            return links

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.Filter, self).get_context_data(*args, **kwargs)

            group = self.derive_group()
            org = self.request.user.get_org()

            if group.is_dynamic:
                actions = ('block', 'label')
            else:
                actions = ('block', 'label', 'unlabel')

            context['actions'] = actions
            context['current_group'] = group
            context['contact_fields'] = ContactField.objects.filter(org=org, is_active=True).order_by('pk')
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<group>[^/]+)/$' % (path, action)

        def derive_group(self):
            return ContactGroup.user_groups.get(uuid=self.kwargs['group'])

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactForm
        exclude = ('is_active', 'uuid', 'language', 'org', 'fields', 'is_blocked', 'is_stopped',
                   'created_by', 'modified_by', 'is_test', 'channel')
        success_message = ''
        submit_button_name = _("Create")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super(ContactCRUDL.Create, self).get_form_kwargs(*args, **kwargs)
            form_kwargs['user'] = self.request.user
            return form_kwargs

        def get_form(self):
            return super(ContactCRUDL.Create, self).get_form()

        def pre_save(self, obj):
            obj = super(ContactCRUDL.Create, self).pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def save(self, obj):
            urns = []
            for field_key, value in six.iteritems(self.form.cleaned_data):
                if field_key.startswith('urn__') and value:
                    scheme = field_key.split('__')[1]
                    urns.append(URN.from_parts(scheme, value))

            Contact.get_or_create_by_urns(obj.org, self.request.user, obj.name, urns)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateContactForm
        exclude = ('is_active', 'uuid', 'org', 'fields', 'is_blocked', 'is_stopped',
                   'created_by', 'modified_by', 'is_test', 'channel')
        success_url = 'uuid@contacts.contact_read'
        success_message = ''
        submit_button_name = _("Save Changes")

        def derive_queryset(self):
            qs = super(ContactCRUDL.Update, self).derive_queryset()
            return qs.filter(is_test=False)

        def derive_exclude(self):
            obj = self.get_object()
            exclude = []
            exclude.extend(self.exclude)

            if not obj.org.primary_language:
                exclude.append('language')

            if obj.is_blocked:
                exclude.append('groups')

            return exclude

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super(ContactCRUDL.Update, self).get_form_kwargs(*args, **kwargs)
            form_kwargs['user'] = self.request.user
            return form_kwargs

        def get_form(self):
            return super(ContactCRUDL.Update, self).get_form()

        def save(self, obj):
            super(ContactCRUDL.Update, self).save(obj)

            new_groups = self.form.cleaned_data.get('groups')
            if new_groups is not None:
                obj.update_static_groups(self.request.user, new_groups)

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Update, self).get_context_data(**kwargs)
            context['schemes'] = ContactURN.SCHEME_CHOICES
            return context

        def post_save(self, obj):
            obj = super(ContactCRUDL.Update, self).post_save(obj)

            if not self.org.is_anon:
                urns = []

                for field_key, value in six.iteritems(self.form.data):
                    if field_key.startswith('urn__') and value:
                        parts = field_key.split('__')
                        scheme = parts[1]

                        order = int(self.form.data.get('order__' + field_key, "0"))
                        urns.append((order, URN.from_parts(scheme, value)))

                new_scheme = self.form.cleaned_data.get('new_scheme', None)
                new_path = self.form.cleaned_data.get('new_path', None)

                if new_scheme and new_path:
                    urns.append((len(urns), URN.from_parts(new_scheme, new_path)))

                # sort our urns by the supplied order
                urns = [urn[1] for urn in sorted(urns, key=lambda x: x[0])]
                obj.update_urns(self.request.user, urns)

            return obj

    class UpdateFields(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = ContactFieldForm
        exclude = ('is_active', 'uuid', 'org', 'fields', 'is_blocked', 'is_stopped',
                   'created_by', 'modified_by', 'is_test', 'channel', 'name', 'language')
        success_url = 'uuid@contacts.contact_read'
        success_message = ''
        submit_button_name = _("Save Changes")

        def get_form_kwargs(self, *args, **kwargs):
            return super(ContactCRUDL.UpdateFields, self).get_form_kwargs(*args, **kwargs)

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.UpdateFields, self).get_context_data(**kwargs)
            org = self.request.user.get_org()
            field_id = self.request.GET.get('field', 0)
            if field_id:
                context['contact_field'] = org.contactfields.get(id=field_id)
            return context

        def post_save(self, obj):
            obj = super(ContactCRUDL.UpdateFields, self).post_save(obj)
            contact_field = obj.org.contactfields.get(id=self.form.cleaned_data.get('contact_field'))
            if contact_field:
                obj.set_field(self.request.user, contact_field.key, self.form.cleaned_data.get('field_value', ''))
            return obj

    class UpdateFieldsInput(OrgObjPermsMixin, SmartReadView):
        """
        Simple view for displaying a form rendered input of a contact field value. This is a helper
        view for UpdateFields to show different inputs based on the selected field.
        """
        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.UpdateFieldsInput, self).get_context_data(**kwargs)
            field_id = self.request.GET.get('field', 0)
            if field_id:
                contact_field = ContactField.objects.filter(id=field_id).first()
                context['contact_field'] = contact_field
                if contact_field:
                    context['value'] = self.get_object().get_field_display(contact_field.key)
            return context

    class Block(OrgPermsMixin, SmartUpdateView):
        """
        Block this contact
        """
        fields = ()
        success_url = 'uuid@contacts.contact_read'
        success_message = _("Contact blocked")

        def save(self, obj):
            obj.block(self.request.user)
            return obj

    class Unblock(OrgPermsMixin, SmartUpdateView):
        """
        Unblock this contact
        """
        fields = ()
        success_url = 'uuid@contacts.contact_read'
        success_message = _("Contact unblocked")

        def save(self, obj):
            obj.unblock(self.request.user)
            return obj

    class Unstop(OrgPermsMixin, SmartUpdateView):
        """
        Unstops this contact
        """
        fields = ()
        success_url = 'uuid@contacts.contact_read'
        success_message = _("Contact unstopped")

        def save(self, obj):
            obj.unstop(self.request.user)
            return obj

    class Delete(OrgPermsMixin, SmartUpdateView):
        """
        Delete this contact (can't be undone)
        """
        fields = ()
        success_url = '@contacts.contact_list'
        success_message = ''

        def save(self, obj):
            obj.release(self.request.user)
            return obj


class ContactGroupCRUDL(SmartCRUDL):
    model = ContactGroup
    actions = ('create', 'update', 'delete')

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactGroupForm
        fields = ('name', 'preselected_contacts', 'group_query')
        success_url = "uuid@contacts.contact_filter"
        success_message = ''
        submit_button_name = _("Create")

        def save(self, obj):
            org = self.request.user.get_org()
            user = self.request.user
            name = self.form.cleaned_data.get('name')
            query = self.form.cleaned_data.get('group_query')
            preselected_contacts = self.form.cleaned_data.get('preselected_contacts')

            if query:
                self.object = ContactGroup.create_dynamic(org, user, name, query)
            else:
                self.object = ContactGroup.create_static(org, user, name)

                if preselected_contacts:
                    preselected_ids = [int(c_id) for c_id in preselected_contacts.split(',') if c_id.isdigit()]
                    contacts = Contact.objects.filter(org=org, pk__in=preselected_ids, is_active=True)

                    self.object.update_contacts(user, contacts, add=True)

        def get_form_kwargs(self):
            kwargs = super(ContactGroupCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = ContactGroupForm
        fields = ('name',)
        success_url = 'uuid@contacts.contact_filter'
        success_message = ''

        def derive_fields(self):
            return ('name', 'query') if self.get_object().is_dynamic else ('name',)

        def get_form_kwargs(self):
            kwargs = super(ContactGroupCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def form_valid(self, form):
            self.prev_query = self.get_object().query

            return super(ContactGroupCRUDL.Update, self).form_valid(form)

        def post_save(self, obj):
            obj = super(ContactGroupCRUDL.Update, self).post_save(obj)

            if obj.query and obj.query != self.prev_query:
                obj.update_query(obj.query)
            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        cancel_url = 'uuid@contacts.contact_filter'
        redirect_url = '@contacts.contact_list'
        success_message = ''
        fields = ('id',)

        def get_context_data(self, **kwargs):
            context = super(ContactGroupCRUDL.Delete, self).get_context_data(**kwargs)
            context['triggers'] = self.get_object().trigger_set.filter(is_archived=False)
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
            flows = Flow.objects.filter(org=group.org, group_dependencies__in=[group])
            if flows.count():
                return HttpResponseRedirect(smart_url(self.cancel_url, group))

            # remove our group
            group.release()

            # make is_active False for all its triggers too
            group.trigger_set.all().update(is_active=False)

            # we can't just redirect so as to make our modal do the right thing
            response = self.render_to_response(self.get_context_data(success_url=self.get_success_url(),
                                                                     success_script=getattr(self, 'success_script', None)))
            response['Temba-Success'] = self.get_success_url()
            return response


class ManageFieldsForm(forms.Form):

    def __init__(self, *args, **kwargs):
        self.org = kwargs['org']
        del kwargs['org']
        super(ManageFieldsForm, self).__init__(*args, **kwargs)

    def clean(self):
        used_labels = []
        for key in self.cleaned_data:
            if key.startswith('field_'):
                idx = key[6:]
                field = self.cleaned_data[key]
                label = self.cleaned_data["label_%s" % idx]

                if label:
                    if not ContactField.is_valid_label(label):
                        raise forms.ValidationError(_("Field names can only contain letters, numbers and hypens"))

                    if label.lower() in used_labels:
                        raise forms.ValidationError(_("Field names must be unique. '%s' is duplicated") % label)

                    elif not ContactField.is_valid_key(ContactField.make_key(label)):
                        raise forms.ValidationError(_("Field name '%s' is a reserved word") % label)
                    used_labels.append(label.lower())
                else:
                    # don't allow fields that are dependencies for flows be removed
                    if field != '__new_field':
                        from temba.flows.models import Flow
                        flow = Flow.objects.filter(org=self.org, field_dependencies__in=[field]).first()
                        if flow:
                            raise forms.ValidationError(_('The field "%s" cannot be removed while it is still used in the flow "%s"' % (field.label, flow.name)))

        return self.cleaned_data


class ContactFieldCRUDL(SmartCRUDL):
    model = ContactField
    actions = ('list', 'managefields', 'json')

    class List(OrgPermsMixin, SmartListView):
        def get_queryset(self, **kwargs):
            qs = super(ContactFieldCRUDL.List, self).get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org(), is_active=True)

            query = self.request.GET.get('search', None)
            if query:
                qs = qs.filter(Q(key__icontains=query) | Q(label__icontains=query))

            qs = qs.order_by('label')
            return qs

    class Json(OrgPermsMixin, SmartListView):
        paginate_by = None

        def get_queryset(self, **kwargs):
            qs = super(ContactFieldCRUDL.Json, self).get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org(), is_active=True)
            return qs

        def render_to_response(self, context, **response_kwargs):
            results = []
            for obj in context['object_list']:
                result = dict(id=obj.pk, key=obj.key, label=obj.label)
                results.append(result)

            sorted_results = sorted(results, key=lambda k: k['label'].lower())

            sorted_results.insert(0, dict(key='groups', label='Groups'))

            for config in reversed(URN_SCHEME_CONFIG):
                sorted_results.insert(0, dict(key=config[3], label=six.text_type(config[1])))

            sorted_results.insert(0, dict(key='name', label='Full name'))

            return HttpResponse(json.dumps(sorted_results), content_type='application/json')

    class Managefields(ModalMixin, OrgPermsMixin, SmartFormView):
        title = _("Manage Contact Fields")
        submit_button_name = _("Update Fields")
        success_url = "@contacts.contact_list"
        form_class = ManageFieldsForm

        def get_context_data(self, **kwargs):
            context = super(ContactFieldCRUDL.Managefields, self).get_context_data(**kwargs)
            num_fields = ContactField.objects.filter(org=self.request.user.get_org(), is_active=True).count()

            contact_fields = []
            for field_idx in range(1, num_fields + 2):
                contact_field = dict(show='show_%d' % field_idx,
                                     type='type_%d' % field_idx,
                                     label='label_%d' % field_idx,
                                     field='field_%d' % field_idx)
                contact_fields.append(contact_field)

            context['contact_fields'] = contact_fields
            return context

        def get_form_kwargs(self):
            kwargs = super(ContactFieldCRUDL.Managefields, self).get_form_kwargs()
            kwargs['org'] = self.derive_org()
            return kwargs

        def get_form(self):
            form = super(ContactFieldCRUDL.Managefields, self).get_form()
            form.fields.clear()

            org = self.request.user.get_org()
            contact_fields = ContactField.objects.filter(org=org, is_active=True).order_by('pk')

            added_fields = []

            i = 1
            for contact_field in contact_fields:
                form_field_label = _("@contact.%(key)s") % {'key': contact_field.key}
                added_fields.append(("show_%d" % i, forms.BooleanField(initial=contact_field.show_in_table, required=False)))
                added_fields.append(("type_%d" % i, forms.ChoiceField(label=' ', choices=Value.TYPE_CHOICES, initial=contact_field.value_type, required=True)))
                added_fields.append(("label_%d" % i, forms.CharField(label=' ', max_length=36, help_text=form_field_label, initial=contact_field.label, required=False)))
                added_fields.append(("field_%d" % i, forms.ModelChoiceField(contact_fields, widget=forms.HiddenInput(), initial=contact_field)))
                i += 1

            # add a last field for the user to add one
            added_fields.append(("show_%d" % i, forms.BooleanField(label=_("show"), initial=False, required=False)))
            added_fields.append(("type_%d" % i, forms.ChoiceField(choices=Value.TYPE_CHOICES, initial=Value.TYPE_TEXT, required=True)))
            added_fields.append(("label_%d" % i, forms.CharField(max_length=36, required=False)))
            added_fields.append(("field_%d" % i, forms.CharField(widget=forms.HiddenInput(), initial="__new_field")))

            form.fields = OrderedDict(list(form.fields.items()) + added_fields)

            return form

        def form_valid(self, form):
            try:
                cleaned_data = form.cleaned_data
                user = self.request.user
                org = user.get_org()

                for key in cleaned_data:
                    if key.startswith('field_'):
                        idx = key[6:]
                        label = cleaned_data["label_%s" % idx]
                        field = cleaned_data[key]
                        show_in_table = cleaned_data["show_%s" % idx]
                        value_type = cleaned_data['type_%s' % idx]

                        if field == '__new_field':
                            if label:
                                analytics.track(user.username, 'temba.contactfield_created')
                                key = ContactField.make_key(label)
                                ContactField.get_or_create(org, user, key, label, show_in_table=show_in_table, value_type=value_type)
                        else:
                            if label:
                                ContactField.get_or_create(org, user, field.key, label, show_in_table=show_in_table, value_type=value_type)
                            else:
                                ContactField.hide_field(org, user, field.key)

                if 'HTTP_X_PJAX' not in self.request.META:
                    return HttpResponseRedirect(self.get_success_url())
                else:  # pragma: no cover
                    return self.render_to_response(self.get_context_data(form=form,
                                                                         success_url=self.get_success_url(),
                                                                         success_script=getattr(self, 'success_script', None)))

            except (IntegrityError, ValueError) as e:  # pragma: no cover
                message = str(e).capitalize()
                errors = self.form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.utils.ErrorList())
                errors.append(message)
                return self.render_to_response(self.get_context_data(form=form))
