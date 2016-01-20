from __future__ import unicode_literals

import json
import pycountry
import regex
import pytz
import time


from collections import OrderedDict
from datetime import timedelta, datetime
from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.core.urlresolvers import reverse
from django.db import IntegrityError
from django.http import Http404, HttpResponseRedirect, HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from itertools import chain
from smartmin.csv_imports.models import ImportTask
from smartmin.views import SmartCreateView, SmartCRUDL, SmartCSVImportView, SmartDeleteView, SmartFormView
from smartmin.views import SmartListView, SmartReadView, SmartUpdateView, SmartXlsView, smart_url
from temba.channels.models import SEND
from temba.contacts.models import Contact, ContactGroup, ContactField, ContactURN, URN_SCHEME_CHOICES, TEL_SCHEME
from temba.contacts.models import ExportContactsTask
from temba.contacts.tasks import export_contacts_task
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.msgs.models import Broadcast, Call, Msg, VISIBLE, ARCHIVED
from temba.msgs.views import SendMessageForm, BaseActionForm
from temba.values.models import VALUE_TYPE_CHOICES, TEXT, DISTRICT
from temba.utils import analytics, slugify_with, languages
from .omnibox import omnibox_query, omnibox_results_to_dict


class RemoveContactForm(forms.Form):
    contact = forms.ModelChoiceField(Contact.objects.all())
    group = forms.ModelChoiceField(ContactGroup.user_groups.all())

    def __init__(self, *args, **kwargs):
        org = kwargs['org']
        del kwargs['org']

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
        group.update_contacts([contact], False)
        return dict(group_id=group.id, contact_id=contact.id)


class ContactGroupForm(forms.ModelForm):
    preselected_contacts = forms.CharField(required=False, widget=forms.HiddenInput)
    group_query = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(ContactGroupForm, self).__init__(*args, **kwargs)

    def clean_name(self):
        data = self.cleaned_data['name'].strip()

        if not ContactGroup.is_valid_name(data):
            raise forms.ValidationError("Group name must not be blank or begin with + or -")

        return data

    class Meta:
        fields = '__all__'
        model = ContactGroup


class ContactListView(OrgPermsMixin, SmartListView):
    """
    Base class for contact list views with contact folders and groups listed by the side
    """
    add_button = True

    def pre_process(self, request, *args, **kwargs):
        if hasattr(self, 'system_group'):
            org = request.user.get_org()
            self.queryset = ContactGroup.get_system_group_queryset(org, self.system_group)

    def get_queryset(self, **kwargs):
        qs = super(ContactListView, self).get_queryset(**kwargs)
        qs = qs.filter(is_test=False)
        org = self.request.user.get_org()

        # contact list views don't use regular field searching but use more complex contact searching
        query = self.request.REQUEST.get('search', None)
        if query:
            qs, self.request.compiled_query = Contact.search(org, query, qs)

        return qs.order_by('-pk').prefetch_related('all_groups')

    def order_queryset(self, queryset):
        """
        Order contacts by name, case insensitive
        """
        return queryset
            
    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = ContactGroup.get_system_group_counts(org)

        # if there isn't a search filtering the queryset, we can replace the count function with a quick cache lookup to
        # speed up paging
        if hasattr(self, 'system_group') and 'search' not in self.request.REQUEST:
            self.object_list.count = lambda: counts[self.system_group]

        context = super(ContactListView, self).get_context_data(**kwargs)

        folders = [dict(count=counts[ContactGroup.TYPE_ALL], label=_("All Contacts"), url=reverse('contacts.contact_list')),
                   dict(count=counts[ContactGroup.TYPE_FAILED], label=_("Failed"), url=reverse('contacts.contact_failed')),
                   dict(count=counts[ContactGroup.TYPE_BLOCKED], label=_("Blocked"), url=reverse('contacts.contact_blocked'))]

        groups_qs = ContactGroup.user_groups.filter(org=org, is_active=True).select_related('org')
        groups_qs = groups_qs.extra(select={'lower_group_name': 'lower(contacts_contactgroup.name)'}).order_by('lower_group_name')
        groups = [dict(pk=g.pk, label=g.name, count=g.get_member_count(), is_dynamic=g.is_dynamic) for g in groups_qs]

        # resolve the paginated object list so we can initialize a cache of URNs and fields
        contacts = list(context['object_list'])
        Contact.bulk_cache_initialize(org, contacts, for_show_only=True)

        context['contacts'] = contacts
        context['groups'] = groups
        context['folders'] = folders
        context['has_contacts'] = contacts or org.has_contacts()
        context['send_form'] = SendMessageForm(self.request.user)
        return context


class ContactActionForm(BaseActionForm):
    ALLOWED_ACTIONS = (('label', _("Add to Group")),
                       ('unlabel', _("Remove from Group")),
                       ('unblock', _("Unblock Contacts")),
                       ('block', _("Block Contacts")),
                       ('delete', _("Delete Contacts")))

    OBJECT_CLASS = Contact
    OBJECT_CLASS_MANAGER = 'objects'
    LABEL_CLASS = ContactGroup
    LABEL_CLASS_MANAGER = 'user_groups'
    HAS_IS_ACTIVE = True

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


class ContactForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.user = kwargs['user']
        self.org = self.user.get_org()
        del kwargs['user']
        super(ContactForm, self).__init__(*args, **kwargs)

    def add_extra_fields(self, inc_contact_fields):
        extra_fields = []

        # add all URN scheme fields if org is not anon
        if not self.org.is_anon:
            # always include phone but other schemes only if they're supported
            supported_urn_choices = [c for c in URN_SCHEME_CHOICES if c[0] in self.org.get_schemes(SEND) or c[0] == TEL_SCHEME]

            for scheme, label in supported_urn_choices:
                help_text = '%s for this contact (@contact.%s)' % (label, scheme)

                # get all the urns for this scheme
                urns = self.instance.get_urns_for_scheme(scheme)
                if urns:
                    for i, urn in enumerate(urns):
                        ctrl = forms.CharField(required=False, label=label, initial=urn.path, help_text=help_text)
                        extra_fields.append(('__urn__%s__%d' % (scheme, i), ctrl))
                        help_text = '%s for this contact' % label

                else:
                    ctrl = forms.CharField(required=False, label=label, initial=None, help_text=help_text)
                    extra_fields.append(('__urn__%s__%d' % (scheme, 0), ctrl))

        # add all contact fields
        if inc_contact_fields:
            for field in ContactField.objects.filter(org=self.org, is_active=True).order_by('label'):
                initial = self.instance.get_field_display(field.key) if self.instance else None
                help_text = 'Custom field (@contact.%s)' % field.key

                ctrl = forms.CharField(required=False, label=field.label, initial=initial, help_text=help_text)
                extra_fields.append(('__field__' + field.key, ctrl))

        self.fields = OrderedDict(self.fields.items() + extra_fields)

    def clean(self):
        country = self.org.get_country_code()

        # validate URN fields
        for field_key, value in self.cleaned_data.iteritems():
            if field_key.startswith('__urn__') and value:
                scheme = field_key[7:field_key.rfind('__')]

                norm_scheme, norm_path = ContactURN.normalize_urn(scheme, value, country)
                existing = Contact.from_urn(self.org, norm_scheme, norm_path)

                if existing and existing != self.instance:
                    self._errors[field_key] = _("Used by another contact")
                elif not ContactURN.validate_urn(norm_scheme, norm_path):
                    self._errors[field_key] = _("Invalid format")

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

        choices += [(lang.iso_code, lang.name) for lang in self.instance.org.languages.all().order_by('orgs', 'name')]

        self.fields['language'] = forms.ChoiceField(required=False, label=_('Language'), initial=self.instance.language, choices=choices)

        self.fields['groups'].initial = self.instance.user_groups.all()
        self.fields['groups'].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields['groups'].help_text = _("The groups which this contact belongs to")


class ContactCRUDL(SmartCRUDL):
    model = Contact
    actions = ('create', 'update', 'failed', 'list', 'import', 'read', 'filter', 'blocked', 'omnibox',
               'customize', 'export', 'block', 'unblock', 'delete', 'history')

    class Export(OrgPermsMixin, SmartXlsView):

        def render_to_response(self, context, **response_kwargs):

            analytics.track(self.request.user.username, 'temba.contact_exported')

            user = self.request.user
            org = user.get_org()

            group = None
            group_id = self.request.REQUEST.get('g', None)
            if group_id:
                groups = ContactGroup.user_groups.filter(pk=group_id, org=org)

                if groups:
                    group = groups[0]

            host = self.request.branding['host']

            # is there already an export taking place?
            existing = ExportContactsTask.objects.filter(org=org, is_finished=False,
                                                         created_on__gt=timezone.now() - timedelta(hours=24))\
                                                 .order_by('-created_on').first()

            # if there is an existing export, don't allow it
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))

            # otherwise, off we go
            else:
                export = ExportContactsTask.objects.create(created_by=user, modified_by=user, org=org,
                                                           group=group, host=host)
                export_contacts_task.delay(export.pk)

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):
                    messages.info(self.request,
                                  _("We are preparing your export. We will e-mail you at %s when it is ready.")
                                  % self.request.user.username)

                else:
                    export = ExportContactsTask.objects.get(id=export.pk)
                    dl_url = reverse('assets.download', kwargs=dict(type='contact_export', pk=export.pk))
                    messages.info(self.request,
                                  _("Export complete, you can find it here: %s (production users will get an email)")
                                  % dl_url)

            return HttpResponseRedirect(reverse('contacts.contact_list'))

    class Customize(OrgPermsMixin, SmartUpdateView):

        class CustomizeForm(forms.ModelForm):
            def clean(self):

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
                            raise ValidationError(_("Field names can only contain letters, numbers, "
                                                    "hypens"))

                        if field_key in Contact.RESERVED_FIELDS:
                            raise ValidationError(_("%s is a reserved name for contact fields") % value)

                        if field_label in used_labels:
                            raise ValidationError(_("%s should be used once") % field_label)

                        used_labels.append(field_label)

                return self.cleaned_data

            class Meta:
                model = ImportTask
                fields = '__all__'

        model = ImportTask
        form_class = CustomizeForm

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
                label_field = forms.CharField(label=' ', initial=header.title())


                label_initial = ContactField.get_by_label(org, header.title())

                label_field_initial = header.title()
                if label_initial:
                    label_field_initial = label_initial.label

                label_field = forms.CharField( initial=label_field_initial,
                                                     required=False, label=' ')

                label_field_name = 'column_%s_label' % header_key

                type_field_initial = None
                if label_initial:
                    type_field_initial = label_initial.value_type

                type_field = forms.ChoiceField(label=' ', choices=VALUE_TYPE_CHOICES, required=True, initial=type_field_initial)
                type_field_name = 'column_%s_type' % header_key

                fields = []
                fields.append((include_field_name, include_field))
                fields.append((label_field_name, label_field))
                fields.append((type_field_name, type_field))

                self.form.fields = OrderedDict(self.form.fields.items() + fields)

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

        def get_form(self, form_class):
            form = super(ContactCRUDL.Customize, self).get_form(form_class)
            form.fields.clear()
            
            self.headers = Contact.get_import_file_headers(self.get_object().csv_file.file)
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

            def clean_csv_file(self):
                try:
                    Contact.get_import_file_headers(ContentFile(self.cleaned_data['csv_file'].read()))
                except Exception as e:
                    raise forms.ValidationError(str(e))

                return self.cleaned_data['csv_file']

            class Meta:
                model = ImportTask
                fields = '__all__'

        form_class = ImportForm
        model = ImportTask
        fields = ('csv_file',)
        success_message = ''

        def post_save(self, task):
            # configure import params with current org and timezone
            org = self.derive_org()
            params = dict(org_id=org.id, timezone=org.timezone, extra_fields=[], original_filename=self.form.cleaned_data['csv_file'].name)
            task.import_params = json.dumps(params)
            task.save()

            headers = Contact.get_import_file_headers(task.csv_file.file)
            if not headers and not task.done():
                task.start()
            return task

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Import, self).get_context_data(**kwargs)
            context['task'] = None
            context['group'] = None
            context['show_form'] = True

            analytics.track(self.request.user.username, 'temba.contact_imported')

            task_id = self.request.REQUEST.get('task', None)
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

                    elif not task.status() in ['PENDING', 'RUNNING', 'STARTED']:
                        context['show_form'] = True

            return context

        def derive_refresh(self):
            task_id = self.request.REQUEST.get('task', None)
            if task_id:
                tasks = ImportTask.objects.filter(pk=task_id, created_by=self.request.user)
                if tasks and tasks[0].status() in ['PENDING', 'RUNNING', 'STARTED']:
                    return 3000
            return 0

        def derive_success_message(self):
            return None

        def get_success_url(self):
            if Contact.get_import_file_headers(self.object.csv_file):
                return reverse("contacts.contact_customize", args=[self.object.pk])

            return reverse("contacts.contact_import") + "?task=%d" % self.object.pk

    class Omnibox(OrgPermsMixin, SmartListView):

        fields = ('id', 'text')

        def get_queryset(self, **kwargs):
            org = self.derive_org()
            return omnibox_query(org, **self.request.REQUEST)

        def get_paginate_by(self, queryset):
            if not self.request.REQUEST.get('search', None):
                return 200

            return super(ContactCRUDL.Omnibox, self).get_paginate_by(queryset)

        def render_to_response(self, context, **response_kwargs):
            org = self.derive_org()
            page = context['page_obj']
            object_list = context['object_list']

            results = omnibox_results_to_dict(org, object_list)

            json_result = {'results': results, 'more': page.has_next(), 'total': len(results), 'err': 'nil'}

            return HttpResponse(json.dumps(json_result), content_type='application/json')

    class Read(OrgObjPermsMixin, SmartReadView):
        fields = ('name',)

        def derive_title(self):
            return self.object.get_display()

        @classmethod
        def derive_url_pattern(cls, path, action):
            # overloaded to have uuid pattern instead of integer id
            return r'^%s/%s/(?P<uuid>[^/]+)/$' % (path, action)

        def get_object(self, queryset=None):
            uuid = self.kwargs.get('uuid')
            if self.request.user.is_superuser:
                contact = Contact.objects.filter(uuid=uuid, is_active=True).first()
            else:
                contact = Contact.objects.filter(uuid=uuid, is_active=True, is_test=False, org=self.request.user.get_org()).first()

            if contact is None:
                raise Http404("No active contact with that UUID")

            return contact

        def get_context_data(self, **kwargs):
            context = super(ContactCRUDL.Read, self).get_context_data(**kwargs)

            contact = self.object
            if not contact.is_active:
                raise Http404("No active contact with that id")

            # the users group membership
            context['contact_groups'] = contact.user_groups.extra(select={'lower_name': 'lower(name)'}).order_by('lower_name')

            # event fires
            event_fires = contact.fire_events.filter(scheduled__gte=timezone.now()).order_by('scheduled')
            scheduled_messages = contact.get_scheduled_messages()

            merged_upcoming_events = []
            for fire in event_fires:
                merged_upcoming_events.append(dict(event_type=fire.event.event_type, message=fire.event.message,
                                                   flow_id=fire.event.flow.pk, flow_name=fire.event.flow.name,
                                                   scheduled=fire.scheduled))

            for sched_broadcast in scheduled_messages:
                merged_upcoming_events.append(dict(repeat_period=sched_broadcast.schedule.repeat_period, event_type='M', message=sched_broadcast.text, flow_id=None,
                                                   flow_name=None, scheduled=sched_broadcast.schedule.next_fire))

            # upcoming scheduled events
            context['upcoming_events'] = sorted(merged_upcoming_events, key=lambda k: k['scheduled'], reverse=True)

            # divide contact's URNs into those we can send to, and those we can't
            from temba.channels.models import SEND
            sendable_schemes = contact.org.get_schemes(SEND)
            sendable_urns = []
            unsendable_urns = []
            for urn in contact.urns.order_by('-priority'):
                if urn.scheme in sendable_schemes:
                    sendable_urns.append(urn)
                else:
                    unsendable_urns.append(urn)
            context['contact_sendable_urns'] = sendable_urns
            context['contact_unsendable_urns'] = unsendable_urns

            # load our contacts values
            Contact.bulk_cache_initialize(contact.org, [contact])

            # lookup all of our contact fields
            contact_fields = []
            fields = ContactField.objects.filter(org=contact.org, is_active=True).order_by('label', 'pk')
            for field in fields:
                value = getattr(contact, '__field__%s' % field.key)
                if value:
                    display = Contact.get_field_display_for_value(field, value)
                    contact_fields.append(dict(label=field.label, value=display, featured=field.show_in_table))

            # stuff in the contact's language in the fields as well
            if contact.language:
                lang = languages.get_language_name(contact.language)
                if not lang:
                    lang = contact.language
                contact_fields.append(dict(label='Language', value=lang, featured=True))

            context['contact_fields'] = sorted(contact_fields, key=lambda f: f['label'])
            context['recent_seconds'] = int(time.mktime((timezone.now() - timedelta(days=7)).timetuple()))
            return context

        def post(self, request, *args, **kwargs):
            form = RemoveContactForm(self.request.POST, org=request.user.get_org())
            if form.is_valid():
                result = form.execute()
                return HttpResponse(json.dumps(result))

            # shouldn't ever happen
            else:  # pragma: no cover
                raise forms.ValidationError(_("Invalid group or contact id"))

        def get_gear_links(self):
            links = []

            if self.has_org_perm("msgs.broadcast_send"):
                links.append(dict(title=_('Send Message'),
                                  style='btn-primary',
                                  href='#',
                                  js_class='contact-send-button'))

            if self.has_org_perm("contacts.contact_update"):

                links.append(dict(title=_('Edit'), style='btn-primary', js_class='update-contact', href="#"))

                if self.has_org_perm("contacts.contact_block") and not self.object.is_blocked:
                    links.append(dict(title=_('Block'), style='btn-primary', js_class='posterize',
                                      href=reverse('contacts.contact_block', args=(self.object.pk,))))

                if self.has_org_perm("contacts.contact_unblock") and self.object.is_blocked:
                    links.append(dict(title=_('Unblock'), style='btn-primary', js_class='posterize',
                                      href=reverse('contacts.contact_unblock', args=(self.object.pk,))))

                if self.has_org_perm("contacts.contact_delete"):
                    links.append(dict(title=_('Delete'), style='btn-primary',
                                      js_class='contact-delete-button', href='#'))

            return links

    class History(OrgObjPermsMixin, SmartReadView):

        @classmethod
        def derive_url_pattern(cls, path, action):
            # overloaded to have uuid pattern instead of integer id
            return r'^%s/%s/(?P<uuid>[^/]+)/$' % (path, action)

        def get_object(self, queryset=None):
            uuid = self.kwargs.get('uuid')
            if self.request.user.is_superuser:
                contact = Contact.objects.filter(uuid=uuid, is_active=True).first()
            else:
                contact = Contact.objects.filter(uuid=uuid, is_active=True, is_test=False, org=self.request.user.get_org()).first()

            if contact is None:
                raise Http404("No active contact with that id")

            return contact

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.History, self).get_context_data(*args, **kwargs)

            from temba.flows.models import FlowRun, Flow
            from temba.campaigns.models import EventFire

            def activity_cmp(a, b):

                if not hasattr(a, 'created_on') and hasattr(a, 'fired'):
                    a.created_on = a.fired

                if not hasattr(b, 'created_on') and hasattr(b, 'fired'):
                    b.created_on = b.fired

                if a.created_on == b.created_on:
                    return 0
                elif a.created_on < b.created_on:
                    return -1
                else:
                    return 1

            contact = self.get_object()

            # determine our start and end time based on the page
            page = int(self.request.REQUEST.get('page', 1))
            msgs_per_page = 100
            start_time = None

            # if we are just grabbing recent history use that window

            recent_seconds = int(self.request.REQUEST.get('rs', 0))
            recent = self.request.REQUEST.get('r', False)
            context['recent_date'] = datetime.utcfromtimestamp(recent_seconds).replace(tzinfo=pytz.utc)

            text_messages = Msg.all_messages.filter(contact=contact.id, visibility__in=(VISIBLE, ARCHIVED)).order_by('-created_on')
            if recent:
                start_time = context['recent_date']
                text_messages = text_messages.filter(created_on__gt=start_time)
                context['recent'] = True

            # other wise, just grab 100 messages within our page (and an extra marker, for determining more)
            else:
                start_message = (page - 1) * msgs_per_page
                end_message = page * msgs_per_page
                text_messages = text_messages[start_message:end_message+1]

            # ignore our lead message past the first page
            count = len(text_messages)
            first_message = 0

            # if we got an extra one at the end too, trim it off
            context['more'] = False
            if count > msgs_per_page and not recent:
                context['more'] = True
                start_time = text_messages[count - 1].created_on
                count -= 1

            # grab up to 100 messages from our first message
            if not recent_seconds:
                text_messages = text_messages[first_message:first_message+100]

            activity = []

            if count > 0:
                # if we don't know our start time, go back to the beginning
                if not start_time:
                    start_time = timezone.datetime(2013, 1, 1, tzinfo=pytz.utc)

                # if we don't know our stop time yet, assume the first message
                if page == 1:
                    end_time = timezone.now()
                else:
                    end_time = text_messages[0].created_on

                context['start_time'] = start_time

                # all of our runs and events
                runs = FlowRun.objects.filter(contact=contact, created_on__lt=end_time, created_on__gt=start_time).exclude(flow__flow_type=Flow.MESSAGE)
                fired = EventFire.objects.filter(contact=contact, scheduled__lt=end_time, scheduled__gt=start_time).exclude(fired=None)

                # missed calls
                calls = Call.objects.filter(contact=contact, created_on__lt=end_time, created_on__gt=start_time)

                # now chain them all together in the same list and sort by time
                activity = sorted(chain(text_messages, runs, fired, calls), cmp=activity_cmp, reverse=True)

            context['activity'] = activity
            return context

    class List(ContactActionMixin, ContactListView):
        title = _("Contacts")
        refresh = 30000
        system_group = ContactGroup.TYPE_ALL

        def get_gear_links(self):
            links = []

            if self.has_org_perm('contacts.contactgroup_create') and self.request.REQUEST.get('search', None):
                links.append(dict(title=_('Save as Group'), js_class='add-dynamic-group', href="#"))

            if self.has_org_perm('contacts.contactfield_managefields'):
                links.append(dict(title=_('Manage Fields'), js_class='manage-fields', href="#"))

            if self.has_org_perm('contacts.contact_export'):
                links.append(dict(title=_('Export'), href=reverse('contacts.contact_export')))
            return links

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.List, self).get_context_data(*args, **kwargs)
            org = self.request.user.get_org()

            context['actions'] = ('label', 'block')
            context['contact_fields'] = ContactField.objects.filter(org=org, is_active=True).order_by('pk')

            if 'compiled_query' in self.request.__dict__:
                context['compiled_query'] = self.request.compiled_query

            return context

    class Blocked(ContactActionMixin, ContactListView):
        title = _("Blocked Contacts")
        template_name = 'contacts/contact_list.haml'
        system_group = ContactGroup.TYPE_BLOCKED

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.Blocked, self).get_context_data(*args, **kwargs)
            context['actions'] = ('unblock', 'delete') if self.has_org_perm("contacts.contact_delete") else ('unblock',)
            return context

    class Failed(ContactActionMixin, ContactListView):
        title = _("Failed Contacts")
        template_name = 'contacts/contact_failed.haml'
        system_group = ContactGroup.TYPE_FAILED

        def get_context_data(self, *args, **kwargs):
            context = super(ContactCRUDL.Failed, self).get_context_data(*args, **kwargs)
            context['actions'] = ['block']
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
                links.append(dict(title=_('Export'),
                                  href='%s?g=%s' % (reverse('contacts.contact_export'), self.kwargs['group'])))

            if self.has_org_perm('contacts.contactgroup_delete'):
                links.append(dict(title=_('Delete Group'),
                                  js_class='delete-contactgroup',
                                  href="#"))
            return links

        def derive_queryset(self, **kwargs):
            group = self.derive_group()
            return Contact.objects.filter(all_groups=group, is_active=True, org=self.request.user.get_org())

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
            return r'^%s/%s/(?P<group>\d+)/$' % (path, action)

        def derive_group(self):
            return ContactGroup.user_groups.get(pk=self.kwargs['group'])

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactForm
        exclude = ('is_active', 'uuid', 'language', 'org', 'fields', 'is_blocked', 'is_failed',
                   'created_by', 'modified_by', 'is_test', 'channel')
        success_message = ''
        submit_button_name = _("Create")

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super(ContactCRUDL.Create, self).get_form_kwargs(*args, **kwargs)
            form_kwargs['user'] = self.request.user
            return form_kwargs

        def get_form(self, form_class):
            form = super(ContactCRUDL.Create, self).get_form(form_class)
            form.add_extra_fields(False)
            return form

        def pre_save(self, obj):
            obj = super(ContactCRUDL.Create, self).pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def save(self, obj):
            urns = []
            for field_key, value in self.form.cleaned_data.iteritems():
                if field_key.startswith('__urn__') and value:
                    scheme = field_key[7:field_key.rfind('__')]
                    urns.append((scheme, value))

            Contact.get_or_create(obj.org, self.request.user, obj.name, urns)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateContactForm
        exclude = ('is_active', 'uuid', 'org', 'fields', 'is_blocked', 'is_failed',
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

        def get_form(self, form_class):
            form = super(ContactCRUDL.Update, self).get_form(form_class)
            form.add_extra_fields(True)
            return form

        def save(self, obj):
            super(ContactCRUDL.Update, self).save(obj)

            new_groups = self.form.cleaned_data.get('groups')
            if new_groups is not None:
                obj.update_groups(new_groups)

            # TODO replace the contact edit dialog with something more substantial that will support multiple URNs with
            # the same scheme

        def post_save(self, obj):
            obj = super(ContactCRUDL.Update, self).post_save(obj)

            if not self.org.is_anon:
                urns = []
                for field_key, value in self.form.cleaned_data.iteritems():
                    if field_key.startswith('__urn__') and value:
                        scheme = field_key[7:field_key.rfind('__')]
                        urns.append((scheme, value))

                obj.update_urns(urns)

            fields_to_save_later = dict()
            for field_key, value in self.form.cleaned_data.iteritems():
                if field_key.startswith('__field__'):
                    key = field_key[9:]
                    contact_field = ContactField.objects.filter(org=self.org, key=key).first()
                    contact_field_type = contact_field.value_type

                    # district values are saved last to validate the states
                    if contact_field_type == DISTRICT:
                        fields_to_save_later[key] = value
                    else:
                        obj.set_field(key, value)

            # now save our district fields
            for key, value in fields_to_save_later.iteritems():
                obj.set_field(key, value)

            return obj

    class Block(OrgPermsMixin, SmartUpdateView):
        """
        Block this contact
        """
        fields = ()
        success_url = 'uuid@contacts.contact_read'
        success_message = _("Contact blocked")

        def save(self, obj):
            obj.block()
            return obj

    class Unblock(OrgPermsMixin, SmartUpdateView):
        """
        Unblock this contact
        """
        fields = ()
        success_url = 'uuid@contacts.contact_read'
        success_message = _("Contact unblocked")

        def save(self, obj):
            obj.unblock()
            return obj

    class Delete(OrgPermsMixin, SmartUpdateView):
        """
        Delete this contact (can't be undone)
        """
        fields = ()
        success_url = '@contacts.contact_list'
        success_message = ''

        def save(self, obj):
            obj.release()
            return obj


class ContactGroupCRUDL(SmartCRUDL):
    model = ContactGroup
    actions = ('read', 'update', 'create', 'delete', 'list')

    class List(OrgPermsMixin, SmartListView):
        pass

    class Read(OrgObjPermsMixin, SmartReadView):
        fields = ('name', 'contacts')

        def get_context_data(self, **kwargs):
            context = super(ContactGroupCRUDL.Read, self).get_context_data(**kwargs)
            group = self.object
            broadcasts = Broadcast.objects.filter(groups=group.id).order_by('-created_on')
            context['broadcasts'] = broadcasts
            return context

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = ContactGroupForm
        fields = ('name', 'preselected_contacts', 'group_query')
        success_url = "id@contacts.contact_filter"
        success_message = ''
        submit_button_name = _("Create")

        def save(self, obj):
            obj.org = self.request.user.get_org()
            self.object = ContactGroup.get_or_create(obj.org, self.request.user, obj.name)

        def post_save(self, obj, *args, **kwargs):
            obj = super(ContactGroupCRUDL.Create, self).post_save(self.object, *args, **kwargs)
            data = self.form.cleaned_data

            # static group with initial contact ids
            if data['preselected_contacts']:
                preselected_ids = [int(c_id) for c_id in data['preselected_contacts'].split(',') if c_id.isdigit()]
                preselected_contacts = Contact.objects.filter(pk__in=preselected_ids, org=obj.org, is_active=True)

                if preselected_contacts:
                    obj.update_contacts(preselected_contacts, True)

            # dynamic group with a query
            elif data['group_query']:
                obj.update_query(data['group_query'])

            return obj

        def get_form_kwargs(self):
            kwargs = super(ContactGroupCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = ContactGroupForm
        fields = ('name',)
        success_url = 'id@contacts.contact_filter'
        success_message = ''

        def derive_fields(self):
            return ('name', 'query') if self.get_object().is_dynamic else ('name',)

        def get_form_kwargs(self):
            kwargs = super(ContactGroupCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def post_save(self, obj):
            obj = super(ContactGroupCRUDL.Update, self).post_save(obj)
            if obj.query:
                obj.update_query(obj.query)
            return obj

    class Delete(OrgObjPermsMixin, SmartDeleteView):
        cancel_url = 'id@contacts.contact_filter'
        redirect_url = '@contacts.contact_list'
        success_message = ''

        def pre_process(self, request, *args, **kwargs):
            group = self.get_object()
            triggers = group.trigger_set.filter(is_archived=False)
            if triggers.count() > 0:
                trigger_list = ', '.join([trigger.__unicode__() for trigger in triggers])
                messages.error(self.request, _("You cannot remove this group while it has active triggers (%s)" % trigger_list))
                return HttpResponseRedirect(smart_url(self.cancel_url, group))
            return super(ContactGroupCRUDL.Delete, self).pre_process(request, *args, **kwargs)

        def post(self, request, *args, **kwargs):
            group = self.get_object()
            group.release()

            # make is_active False for all its triggers too
            group.trigger_set.all().update(is_active=False)

            return HttpResponseRedirect(reverse("contacts.contact_list"))


class ManageFieldsForm(forms.Form):
    def clean(self):
        used_labels = []
        for key in self.cleaned_data:
            if key.startswith('field_'):
                idx = key[6:]
                label = self.cleaned_data["label_%s" % idx]

                if label:
                    if not ContactField.is_valid_label(label):
                        raise forms.ValidationError(_("Field names can only contain letters, numbers and hypens"))

                    if label.lower() in used_labels:
                        raise ValidationError(_("Field names must be unique"))

                    elif not ContactField.is_valid_key(ContactField.make_key(label)):
                        raise forms.ValidationError(_("Field name '%s' is a reserved word") % label)
                    used_labels.append(label.lower())

        return self.cleaned_data


class ContactFieldCRUDL(SmartCRUDL):
    model = ContactField
    actions = ('managefields', 'json')

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
            return HttpResponse(json.dumps(sorted_results), content_type='application/javascript')

    class Managefields(ModalMixin, OrgPermsMixin, SmartFormView):
        title = _("Manage Contact Fields")
        submit_button_name = _("Update Fields")
        success_url = "@contacts.contact_list"
        form_class = ManageFieldsForm

        def get_context_data(self, **kwargs):
            context = super(ContactFieldCRUDL.Managefields, self).get_context_data(**kwargs)
            num_fields = ContactField.objects.filter(org=self.request.user.get_org(), is_active=True).count()

            contact_fields = []
            for field_idx in range(1, num_fields+2):
                contact_field = dict(show='show_%d' % field_idx,
                                     type='type_%d' % field_idx,
                                     label='label_%d' % field_idx,
                                     field='field_%d' % field_idx)
                contact_fields.append(contact_field)

            context['contact_fields'] = contact_fields
            return context

        def get_form(self, form_class):
            form = super(ContactFieldCRUDL.Managefields, self).get_form(form_class)
            form.fields.clear()

            org = self.request.user.get_org()
            contact_fields = ContactField.objects.filter(org=org, is_active=True).order_by('pk')

            added_fields = []

            i = 1
            for contact_field in contact_fields:
                form_field_label = _("@contact.%(key)s") % {'key' : contact_field.key }
                added_fields.append(("show_%d" % i, forms.BooleanField(initial=contact_field.show_in_table, required=False)))
                added_fields.append(("type_%d" % i, forms.ChoiceField(label=' ', choices=VALUE_TYPE_CHOICES, initial=contact_field.value_type, required=True)))
                added_fields.append(("label_%d" % i, forms.CharField(label=' ', max_length=36, help_text=form_field_label, initial=contact_field.label, required=False)))
                added_fields.append(("field_%d" % i, forms.ModelChoiceField(contact_fields, widget=forms.HiddenInput(), initial=contact_field)))
                i += 1

            # add a last field for the user to add one
            added_fields.append(("show_%d" % i, forms.BooleanField(label=_("show"), initial=False, required=False)))
            added_fields.append(("type_%d" % i, forms.ChoiceField(choices=VALUE_TYPE_CHOICES, initial=TEXT, required=True)))
            added_fields.append(("label_%d" % i, forms.CharField(max_length=36, required=False)))
            added_fields.append(("field_%d" % i, forms.CharField(widget=forms.HiddenInput(), initial="__new_field")))

            form.fields = OrderedDict(form.fields.items() + added_fields)

            return form

        def form_valid(self, form):
            try:
                cleaned_data = form.cleaned_data
                org = self.request.user.get_org()

                for key in cleaned_data:
                    if key.startswith('field_'):
                        idx = key[6:]
                        label = cleaned_data["label_%s" % idx]
                        field = cleaned_data[key]
                        show_in_table = cleaned_data["show_%s" % idx]
                        value_type = cleaned_data['type_%s' % idx]

                        if field == '__new_field':
                            if label:
                                analytics.track(self.request.user.username, 'temba.contactfield_created')
                                key = ContactField.make_key(label)
                                ContactField.get_or_create(org, key, label, show_in_table=show_in_table, value_type=value_type)
                        else:
                            if label:
                                ContactField.get_or_create(org, field.key, label, show_in_table=show_in_table, value_type=value_type)
                            else:
                                ContactField.hide_field(org, field.key)

                if 'HTTP_X_PJAX' not in self.request.META:
                    return HttpResponseRedirect(self.get_success_url())
                else:  # pragma: no cover
                    return self.render_to_response(self.get_context_data(form=form,
                                                                         success_url=self.get_success_url(),
                                                                         success_script=getattr(self, 'success_script', None)))

            except IntegrityError as e:  # pragma: no cover
                message = str(e).capitalize()
                errors = self.form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.utils.ErrorList())
                errors.append(message)
                return self.render_to_response(self.get_context_data(form=form))
