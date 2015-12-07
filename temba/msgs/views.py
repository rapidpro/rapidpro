from __future__ import unicode_literals

import json

from datetime import date, timedelta
from django import forms
from django.conf import settings
from django.core.urlresolvers import reverse
from django.contrib import messages
from django.db import IntegrityError
from django.forms import Form
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.template import Context
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartCreateView, SmartCRUDL, SmartDeleteView, SmartFormView, SmartListView, SmartReadView, SmartUpdateView
from temba.contacts.fields import OmniboxField
from temba.contacts.models import ContactGroup, TEL_SCHEME
from temba.formax import FormaxMixin
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.channels.models import Channel, SEND
from temba.utils import analytics
from temba.utils.expressions import get_function_listing
from .models import Broadcast, Call, ExportMessagesTask, Label, Msg, Schedule, SystemLabel, VISIBLE


def send_message_auto_complete_processor(request):
    completions = []
    user = request.user
    org = None

    if hasattr(user, 'get_org'):
        org = request.user.get_org()

    if org:
        for field in org.contactfields.filter(is_active=True):
            completions.append(dict(name="contact.%s" % str(field.key), display=unicode(_("Contact Field: %(label)s")) % {'label':field.label}))

        completions.insert(0, dict(name='contact', display=unicode(_("Contact Name"))))
        completions.insert(1, dict(name='contact.first_name', display=unicode(_("Contact First Name"))))
        completions.insert(2, dict(name='contact.groups', display=unicode(_("Contact Groups"))))
        completions.insert(3, dict(name='contact.language', display=unicode(_("Contact Language"))))
        completions.insert(4, dict(name='contact.name', display=unicode(_("Contact Name"))))
        completions.insert(5, dict(name='contact.tel', display=unicode(_("Contact Phone"))))
        completions.insert(6, dict(name='contact.tel_e164', display=unicode(_("Contact Phone - E164"))))
        completions.insert(7, dict(name='contact.uuid', display=unicode(_("Contact UUID"))))

        completions.insert(8, dict(name="date", display=unicode(_("Current Date and Time"))))
        completions.insert(9, dict(name="date.now", display=unicode(_("Current Date and Time"))))
        completions.insert(10, dict(name="date.today", display=unicode(_("Current Date"))))
        completions.insert(11, dict(name="date.tomorrow", display=unicode(_("Tomorrow's Date"))))
        completions.insert(12, dict(name="date.yesterday", display=unicode(_("Yesterday's Date"))))

    function_completions = get_function_listing()
    return dict(completions=json.dumps(completions), function_completions=json.dumps(function_completions))


class SendMessageForm(Form):
    omnibox = OmniboxField()
    text = forms.CharField(widget=forms.Textarea, max_length=640)
    schedule = forms.BooleanField(widget=forms.HiddenInput, required=False)

    def __init__(self, user, *args, **kwargs):
        super(SendMessageForm, self).__init__(*args, **kwargs)
        self.fields['omnibox'].set_user(user)

    def is_valid(self):
        valid = super(SendMessageForm, self).is_valid()
        if valid:
            if 'omnibox' not in self.data or len(self.data['omnibox'].strip()) == 0:
                self.errors['__all__'] = self.error_class([unicode(_("At least one recipient is required"))])
                return False
        return valid


class MsgListView(OrgPermsMixin, SmartListView):
    """
    Base class for message list views with message folders and labels listed by the side
    """
    refresh = 10000
    add_button = True
    fields = ('from', 'message', 'received')
    search_fields = ('text__icontains', 'contact__name__icontains', 'contact__urns__path__icontains')
    paginate_by = 100

    def pre_process(self, request, *args, **kwargs):
        if hasattr(self, 'system_label'):
            org = request.user.get_org()
            self.queryset = SystemLabel.get_queryset(org, self.system_label)

    def get_queryset(self, **kwargs):
        queryset = super(MsgListView, self).get_queryset(**kwargs)

        # if we are searching, limit to last 90
        if 'search' in self.request.REQUEST:
            last_90 = timezone.now() - timedelta(days=90)
            queryset = queryset.filter(created_on__gte=last_90)

        return queryset

    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = SystemLabel.get_counts(org)

        # if there isn't a search filtering the queryset, we can replace the count function with a quick cache lookup to
        # speed up paging
        if hasattr(self, 'system_label') and 'search' not in self.request.REQUEST:
            self.object_list.count = lambda: counts[self.system_label]

        context = super(MsgListView, self).get_context_data(**kwargs)

        folders = [dict(count=counts[SystemLabel.TYPE_INBOX], label=_("Inbox"), url=reverse('msgs.msg_inbox')),
                   dict(count=counts[SystemLabel.TYPE_FLOWS], label=_("Flows"), url=reverse('msgs.msg_flow')),
                   dict(count=counts[SystemLabel.TYPE_ARCHIVED], label=_("Archived"), url=reverse('msgs.msg_archived')),
                   dict(count=counts[SystemLabel.TYPE_OUTBOX], label=_("Outbox"), url=reverse('msgs.msg_outbox')),
                   dict(count=counts[SystemLabel.TYPE_SENT], label=_("Sent"), url=reverse('msgs.msg_sent')),
                   dict(count=counts[SystemLabel.TYPE_CALLS], label=_("Calls"), url=reverse('msgs.call_list')),
                   dict(count=counts[SystemLabel.TYPE_SCHEDULED], label=_("Schedules"), url=reverse('msgs.broadcast_schedule_list')),
                   dict(count=counts[SystemLabel.TYPE_FAILED], label=_("Failed"), url=reverse('msgs.msg_failed'))]

        context['folders'] = folders
        context['labels'] = Label.get_hierarchy(org)
        context['has_labels'] = Label.label_objects.filter(org=org).exists()
        context['has_messages'] = org.has_messages() or self.object_list.count() > 0
        context['send_form'] = SendMessageForm(self.request.user)
        return context


class BroadcastForm(forms.ModelForm):
    message = forms.CharField(required=True, widget=forms.Textarea, max_length=160)
    omnibox = OmniboxField()

    def __init__(self, user, *args, **kwargs):
        super(BroadcastForm, self).__init__(*args, **kwargs)
        self.fields['omnibox'].set_user(user)

    def is_valid(self):
        valid = super(BroadcastForm, self).is_valid()
        if valid:
            if 'omnibox' not in self.data or len(self.data['omnibox'].strip()) == 0:
                self.errors['__all__'] = self.error_class([_("At least one recipient is required")])
                return False
            else:
                print "omni: '%s'" % self.data['omnibox']

        return valid

    class Meta:
        model = Broadcast
        fields = '__all__'


class BroadcastCRUDL(SmartCRUDL):
    actions = ('send', 'update', 'schedule_read', 'schedule_list')
    model = Broadcast

    class ScheduleRead(FormaxMixin, OrgObjPermsMixin, SmartReadView):
        title = _("Schedule Message")

        def derive_title(self):
            return _("Scheduled Message")

        def get_context_data(self, **kwargs):
            context = super(BroadcastCRUDL.ScheduleRead, self).get_context_data(**kwargs)
            context['object_list'] = self.get_object().children.all()
            return context

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm('msgs.broadcast_update'):
                formax.add_section('contact', reverse('msgs.broadcast_update', args=[self.object.pk]), icon='icon-megaphone')

            if self.has_org_perm('schedules.schedule_update'):
                action = 'formax'
                if len(self.get_object().children.all()) == 0:
                    action = 'fixed'
                formax.add_section('schedule', reverse('schedules.schedule_update', args=[self.object.schedule.pk]), icon='icon-calendar', action=action)

    class Update(OrgObjPermsMixin, SmartUpdateView):
        form_class = BroadcastForm
        fields = ('message', 'omnibox')
        field_config = {'restrict':{'label':''}, 'omnibox':{'label':''}, 'message':{'label':'', 'help':''},}
        success_message = ''
        success_url = 'msgs.broadcast_schedule_list'

        def get_form_kwargs(self):
            args = super(BroadcastCRUDL.Update, self).get_form_kwargs()
            args['user'] = self.request.user
            return args

        def derive_initial(self):
            selected = ['g-%d' % _.pk for _ in self.object.groups.all()]
            selected += ['c-%d' % _.pk for _ in self.object.contacts.all()]
            selected = ','.join(selected)
            message = self.object.text
            return dict(message=message, omnibox=selected)

        def save(self, *args, **kwargs):
            form = self.form
            broadcast = self.object

            # save off our broadcast info
            omnibox = form.cleaned_data['omnibox']

            # set our new message
            broadcast.text = form.cleaned_data['message']
            broadcast.update_recipients(list(omnibox['groups']) + list(omnibox['contacts']) + list(omnibox['urns']))

            broadcast.save()
            return broadcast

    class ScheduleList(MsgListView):
        refresh = 30000
        title = _("Scheduled Messages")
        fields = ('contacts', 'msgs', 'sent', 'status')
        search_fields = ('text__icontains', 'contacts__urns__path__icontains')
        template_name = 'msgs/broadcast_schedule_list.haml'
        default_order = ('schedule__status', 'schedule__next_fire', '-created_on')
        system_label = SystemLabel.TYPE_SCHEDULED

        def get_queryset(self, **kwargs):
            qs = super(BroadcastCRUDL.ScheduleList, self).get_queryset(**kwargs)
            return qs.select_related('schedule').order_by('-created_on')

    class Send(OrgPermsMixin, SmartFormView):
        title = _("Send Message")
        form_class = SendMessageForm
        fields = ('omnibox', 'text', 'schedule')
        success_url = '@msgs.msg_inbox'
        submit_button_name = _('Send')

        def get_context_data(self, **kwargs):
            context = super(BroadcastCRUDL.Send, self).get_context_data(**kwargs)
            return context

        def pre_process(self, *args, **kwargs):
            response = super(BroadcastCRUDL.Send, self).pre_process(*args, **kwargs)
            org = self.request.user.get_org()
            simulation = self.request.REQUEST.get('simulation', 'false') == 'true'

            if simulation:
                return response

            # can this org send to any URN schemes?
            if not org.get_schemes(SEND):
                return HttpResponseBadRequest("You must add a phone number before sending messages")

            return response

        def derive_success_message(self):
            if 'from_contact' not in self.request.REQUEST:
                return super(BroadcastCRUDL.Send, self).derive_success_message()
            else:
                return None

        def get_success_url(self):
            success_url = super(BroadcastCRUDL.Send, self).get_success_url()
            if 'from_contact' in self.request.REQUEST:
                contact = self.form.cleaned_data['omnibox']['contacts'][0]
                success_url = reverse('contacts.contact_read', args=[contact.uuid])
            return success_url

        def form_invalid(self, form):
            if '_format' in self.request.REQUEST and self.request.REQUEST['_format'] == 'json':
                return HttpResponse(json.dumps(dict(status="error", errors=form.errors)), content_type='application/json', status=400)
            else:
                return super(BroadcastCRUDL.Send, self).form_invalid(form)

        def form_valid(self, form):
            self.form = form
            user = self.request.user
            simulation = self.request.REQUEST.get('simulation', 'false') == 'true'

            omnibox = self.form.cleaned_data['omnibox']
            has_schedule = self.form.cleaned_data['schedule']

            groups = list(omnibox['groups'])
            contacts = list(omnibox['contacts'])
            urns = list(omnibox['urns'])
            recipients = list()

            if simulation:
                # when simulating make sure we only use test contacts
                for contact in contacts:
                    if contact.is_test:
                        recipients.append(contact)
            else:
                for group in groups:
                    recipients.append(group)
                for contact in contacts:
                    recipients.append(contact)
                for urn in urns:
                    recipients.append(urn)

            schedule = Schedule.objects.create(created_by=user, modified_by=user) if has_schedule else None
            broadcast = Broadcast.create(user.get_org(), user, self.form.cleaned_data['text'], recipients,
                                         schedule=schedule)

            if not has_schedule:
                self.post_save(broadcast)
                super(BroadcastCRUDL.Send, self).form_valid(form)

            analytics.track(self.request.user.username, 'temba.broadcast_created',
                            dict(contacts=len(contacts), groups=len(groups), urns=len(urns)))

            if '_format' in self.request.REQUEST and self.request.REQUEST['_format'] == 'json':
                data = dict(status="success", redirect=reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
                return HttpResponse(json.dumps(data), content_type='application/json')
            else:
                if self.form.cleaned_data['schedule']:
                    return HttpResponseRedirect(reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
                return HttpResponseRedirect(self.get_success_url())

        def post_save(self, obj):
            # fire our send in celery
            from temba.msgs.tasks import send_broadcast_task
            send_broadcast_task.delay(obj.pk)
            return obj

        def get_form_kwargs(self):
            kwargs = super(BroadcastCRUDL.Send, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs


class BaseActionForm(forms.Form):
    ALLOWED_ACTIONS = (('label', _("Label Messages")),
                       ('archive', _("Archive Messages")),
                       ('inbox', _("Move to Inbox")),
                       ('resend', _("Resend Messages")),
                       ('delete', _("Delete Messages")))

    OBJECT_CLASS = Msg
    LABEL_CLASS = Label
    LABEL_CLASS_MANAGER = 'all_objects'
    HAS_IS_ACTIVE = False

    action = forms.ChoiceField(choices=ALLOWED_ACTIONS)
    label = forms.ModelChoiceField(getattr(LABEL_CLASS, LABEL_CLASS_MANAGER).all(), required=False)
    objects = forms.ModelMultipleChoiceField(OBJECT_CLASS.objects.all())
    add = forms.BooleanField(required=False)
    number = forms.BooleanField(required=False)

    def __init__(self, *args, **kwargs):
        org = kwargs['org']
        self.user = kwargs['user']
        del kwargs['org']
        del kwargs['user']
        super(BaseActionForm, self).__init__(*args, **kwargs)

        self.fields['action'].choices = self.ALLOWED_ACTIONS
        self.fields['label'].queryset = getattr(self.LABEL_CLASS, self.LABEL_CLASS_MANAGER).filter(org=org)

        self.fields['objects'].queryset = self.OBJECT_CLASS.objects.filter(org=org)
        if self.HAS_IS_ACTIVE:
            self.fields['objects'].queryset = self.OBJECT_CLASS.objects.filter(org=org, is_active=True)

    def clean(self):
        data = self.cleaned_data
        action = data['action']

        update_perm_codename = self.OBJECT_CLASS.__name__.lower() + "_update"

        update_allowed = self.user.get_org_group().permissions.filter(codename=update_perm_codename)
        delete_allowed = self.user.get_org_group().permissions.filter(codename="msg_update")
        resend_allowed = self.user.get_org_group().permissions.filter(codename="broadcast_send")


        if action in ['label', 'unlabel', 'archive', 'restore', 'block', 'unblock'] and not update_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'delete' and not delete_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'resend' and not resend_allowed:
            raise forms.ValidationError(_("Sorry you have no permission for this action."))

        if action == 'label' and 'label' not in self.cleaned_data:
            raise forms.ValidationError(_("Must specify a label"))

        if action == 'unlabel' and 'label' not in self.cleaned_data:
            raise forms.ValidationError(_("Must specify a label"))

        return data

    def execute(self):
        data = self.cleaned_data
        action = data['action']
        objects = data['objects']

        if action == 'label':
            label = data['label']
            add = data['add']

            if not label:
                return dict(error=_("Missing label"))

            changed = self.OBJECT_CLASS.apply_action_label(objects, label, add)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == 'unlabel':
            label = data['label']
            add = data['add']

            if not label:
                return dict(error=_("Missing label"))

            changed = self.OBJECT_CLASS.apply_action_label(objects, label, False)
            return dict(changed=changed, added=add, label_id=label.id, label=label.name)

        elif action == 'archive':
            changed = self.OBJECT_CLASS.apply_action_archive(objects)
            return dict(changed=changed)

        elif action == 'block':
            changed = self.OBJECT_CLASS.apply_action_block(objects)
            return dict(changed=changed)

        elif action == 'unblock':
            changed = self.OBJECT_CLASS.apply_action_unblock(objects)
            return dict(changed=changed)

        elif action == 'restore':
            changed = self.OBJECT_CLASS.apply_action_restore(objects)
            return dict(changed=changed)

        elif action == 'delete':
            changed = self.OBJECT_CLASS.apply_action_delete(objects)
            return dict(changed=changed)

        elif action == 'resend':
            changed = self.OBJECT_CLASS.apply_action_resend(objects)
            return dict(changed=changed)

        # should never make it here
        else:  # pragma: no cover
            return dict(error=_("Oops, so sorry. Something went wrong!"))

        # no action means no-op
        return dict()  # pragma: no cover


class MsgActionForm(BaseActionForm):
    ALLOWED_ACTIONS = (('label', _("Label Messages")),
                       ('archive', _("Archive Messages")),
                       ('restore', _("Move to Inbox")),
                       ('resend', _("Resend Messages")),
                       ('delete', _("Delete Messages")))

    OBJECT_CLASS = Msg
    LABEL_CLASS = Label
    LABEL_CLASS_MANAGER = 'label_objects'

    HAS_IS_ACTIVE = False

    class Meta:
        fields = ('action', 'label', 'objects', 'add', 'number')


class MsgActionMixin(SmartListView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(MsgActionMixin, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        org = user.get_org()

        form = MsgActionForm(self.request.POST, org=org, user=user)

        if form.is_valid():
            response = form.execute()

            # shouldn't get in here in normal operation
            if response and 'error' in response:  # pragma: no-cover
                return HttpResponse(json.dumps(response), content_type='application/json', status=400)

        return self.get(request, *args, **kwargs)


class TestMessageForm(forms.Form):
    channel = forms.ModelChoiceField(Channel.objects.filter(id__lt=0),
                                     help_text=_("Which channel will deliver the message"))
    urn = forms.CharField(max_length=14,
                          help_text=_("The URN of the contact delivering this message"))
    text = forms.CharField(max_length=160, widget=forms.Textarea,
                           help_text=_("The message that is being delivered"))

    def __init__(self, *args, **kwargs):
        org = kwargs['org']
        del kwargs['org']

        super(TestMessageForm, self).__init__(*args, **kwargs)
        self.fields['channel'].queryset = Channel.objects.filter(org=org, is_active=True)


class ExportForm(Form):
    groups = forms.ModelMultipleChoiceField(queryset=ContactGroup.user_groups.filter(pk__lt=0),
                                            required=False, label=_("Groups"))
    start_date = forms.DateField(required=False,
                                 help_text=_("The date for the oldest message to export. (Leave blank to export from the oldest message)."))
    end_date = forms.DateField(required=False,
                               help_text=_("The date for the latest message to export. (Leave blank to export up to the latest message)."))

    def __init__(self, user, *args, **kwargs):
        super(ExportForm, self).__init__(*args, **kwargs)
        self.user = user
        self.fields['groups'].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields['groups'].help_text = _("Export only messages from these contact groups. (Leave blank to export all messages).")

    def clean(self):
        cleaned_data = super(ExportForm, self).clean()
        start_date = cleaned_data['start_date']
        end_date = cleaned_data['end_date']

        if start_date and start_date > date.today():
            raise forms.ValidationError(_("The Start Date should not be a date in the future."))


        if end_date and start_date and end_date <= start_date:
            raise forms.ValidationError(_("The End Date should be a date after the Start Date"))

        return cleaned_data


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = ('inbox', 'flow', 'archived', 'outbox', 'sent', 'failed', 'filter', 'test', 'export')

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@msgs.msg_inbox"

        def get_success_url(self):
            label_id = self.request.REQUEST.get('label', None)

            if label_id:
                return reverse('msgs.msg_filter', args=[label_id])
            return reverse('msgs.msg_inbox')

        def form_invalid(self, form):
            if '_format' in self.request.REQUEST and self.request.REQUEST['_format'] == 'json':
                return HttpResponse(json.dumps(dict(status="error", errors=form.errors)), content_type='application/json', status=400)
            else:
                return super(MsgCRUDL.Export, self).form_invalid(form)

        def form_valid(self, form):
            from temba.msgs.tasks import export_sms_task

            user = self.request.user
            org = user.get_org()

            label_id = self.request.REQUEST.get('label', None)

            label = None
            if label_id:
                label = Label.label_objects.get(pk=label_id)

            host = self.request.branding['host']

            groups = form.cleaned_data['groups']
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']

            # is there already an export taking place?
            existing = ExportMessagesTask.objects.filter(org=org, is_finished=False,
                                                         created_on__gt=timezone.now() - timedelta(hours=24))\
                                                 .order_by('-created_on').first()

            # if there is an existing export, don't allow it
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))

            # otherwise, off we go
            else:
                export = ExportMessagesTask.objects.create(created_by=user, modified_by=user, org=org, host=host,
                                                           label=label, start_date=start_date, end_date=end_date)
                for group in groups:
                    export.groups.add(group)

                export_sms_task.delay(export.pk)

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):
                    messages.info(self.request, _("We are preparing your export. ") +
                                                _("We will e-mail you at %s when it is ready.") % self.request.user.username)

                else:
                    export = ExportMessagesTask.objects.get(id=export.pk)
                    dl_url = reverse('assets.download', kwargs=dict(type='message_export', pk=export.pk))
                    messages.info(self.request, _("Export complete, you can find it here: %s (production users will get an email)") % dl_url)

            try:
                messages.success(self.request, self.derive_success_message())

                if 'HTTP_X_PJAX' not in self.request.META:
                    return HttpResponseRedirect(self.get_success_url())
                else:  # pragma: no cover
                    response = self.render_to_response(self.get_context_data(form=form,
                                                                             success_url=self.get_success_url(),
                                                                             success_script=getattr(self, 'success_script', None)))
                    response['Temba-Success'] = self.get_success_url()
                    response['REDIRECT'] = self.get_success_url()
                    return response

            except IntegrityError as e:  # pragma: no cover
                message = str(e).capitalize()
                errors = self.form._errors.setdefault(forms.forms.NON_FIELD_ERRORS, forms.utils.ErrorList())
                errors.append(message)
                return self.render_to_response(self.get_context_data(form=form))

        def get_form_kwargs(self):
            kwargs = super(MsgCRUDL.Export, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

    class Test(SmartFormView):
        form_class = TestMessageForm
        fields = ('channel', 'urn', 'text')
        title = "Test Message Delivery"
        permissions = 'msgs.msg_test'

        def form_valid(self, *args, **kwargs):
            data = self.form.cleaned_data
            handled = Msg.create_incoming(data['channel'],
                                          (TEL_SCHEME, data['urn']),
                                          data['text'],
                                          user=self.request.user)

            kwargs = self.get_form_kwargs()
            kwargs['initial'] = data
            next_form = TestMessageForm(**kwargs)

            context = self.get_context_data()
            context['handled'] = handled
            context['form'] = next_form
            context['responses'] = handled.responses.all()

            # passing a minimal base template and a simple Context (instead of RequestContext) helps us
            # minimize number of other queries, allowing us to more easily measure queries per request
            context['base_template'] = 'msgs/msg_test_frame.html'
            return self.render_to_response(Context(context))

        def get_form_kwargs(self ,*args, **kwargs):
            kwargs = super(MsgCRUDL.Test, self).get_form_kwargs(*args, **kwargs)
            kwargs['org'] = self.request.user.get_org()
            return kwargs

    class Inbox(MsgActionMixin, MsgListView):
        title = _("Inbox")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_INBOX

        def get_gear_links(self):
            links = []
            if self.has_org_perm('msgs.msg_export'):
                links.append(dict(title=_('Export'),
                                  href='#',
                                  js_class="msg-export-btn"))
            return links

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Inbox, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Inbox, self).get_context_data(*args, **kwargs)
            context['actions'] = ['archive', 'label']
            context['org'] = self.request.user.get_org()
            return context

    class Flow(MsgActionMixin, MsgListView):
        title = _("Flow Messages")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_FLOWS

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Flow, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Flow, self).get_context_data(*args, **kwargs)
            context['actions'] = ['label']
            return context

    class Archived(MsgActionMixin, MsgListView):
        title = _("Archived")
        template_name = 'msgs/msg_archived.haml'
        system_label = SystemLabel.TYPE_ARCHIVED

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Archived, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Archived, self).get_context_data(*args, **kwargs)
            context['actions'] = ['restore', 'label', 'delete']
            return context

    class Outbox(MsgActionMixin, MsgListView):
        title = _("Outbox Messages")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_OUTBOX

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Outbox, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Outbox, self).get_context_data(*args, **kwargs)
            context['actions'] = []
            return context

    class Sent(MsgActionMixin, MsgListView):
        title = _("Sent Messages")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_SENT

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Sent, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Sent, self).get_context_data(*args, **kwargs)
            context['actions'] = []
            return context

    class Failed(MsgActionMixin, MsgListView):
        title = _("Failed Outgoing Messages")
        template_name = 'msgs/msg_failed.haml'
        success_message = ''
        system_label = SystemLabel.TYPE_FAILED

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Failed, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Failed, self).get_context_data(*args, **kwargs)
            context['actions'] = ['resend']
            return context

    class Filter(MsgActionMixin, MsgListView):
        template_name = 'msgs/msg_filter.haml'

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def get_gear_links(self):
            links = []

            edit_btn_cls = 'folder-update-btn' if self.derive_label().is_folder() else 'label-update-btn'

            if self.has_org_perm('msgs.msg_update'):
                links.append(dict(title=_('Edit'),
                                  href='#',
                                  js_class=edit_btn_cls))

            if self.has_org_perm('msgs.msg_export'):
                links.append(dict(title=_('Export Data'),
                                  href='#',
                                  js_class="msg-export-btn"))

            if self.has_org_perm('msgs.broadcast_send'):
                links.append(dict(title=_('Send All'),
                                  style='btn-primary',
                                  href="#",
                                  js_class='filter-send-all-send-button'))

            if self.has_org_perm('msgs.label_delete'):
                links.append(dict(title=_('Remove'), href="#", js_class='remove-label'))

            return links

        def get_context_data(self, *args, **kwargs):
            context = super(MsgCRUDL.Filter, self).get_context_data(*args, **kwargs)
            current_label = self.derive_label()

            # if we're not searching, use pre-calculated count to speed up paging
            if 'search' not in self.request.GET:
                self.object_list.count = lambda: current_label.get_visible_count()

            context['actions'] = ['unlabel', 'label']
            context['current_label'] = current_label
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<label_id>\d+)/$' % (path, action)

        def derive_label(self):
            return Label.all_objects.get(pk=self.kwargs['label_id'])

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Filter, self).get_queryset(**kwargs)
            qs = self.derive_label().filter_messages(qs).filter(visibility=VISIBLE)

            return qs.order_by('-created_on').prefetch_related('labels', 'steps__run__flow').select_related('contact')


class BaseLabelForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data['name']

        if not Label.is_valid_name(name):
            raise forms.ValidationError("Name must not be blank or begin with punctuation")

        existing_id = self.existing.pk if self.existing else None
        if Label.all_objects.filter(org=self.org, name__iexact=name).exclude(pk=existing_id).exists():
            raise forms.ValidationError("Name must be unique")

        return name

    class Meta:
        model = Label
        fields = '__all__'


class LabelForm(BaseLabelForm):
    folder = forms.ModelChoiceField(Label.folder_objects.none(), required=False, label=_("Folder"))
    messages = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop('org')
        self.existing = kwargs.pop('object', None)

        super(LabelForm, self).__init__(*args, **kwargs)

        self.fields['folder'].queryset = Label.folder_objects.filter(org=self.org)


class FolderForm(BaseLabelForm):
    name = forms.CharField(label=_("Name"), help_text=_("The name of this folder"))

    def __init__(self, *args, **kwargs):
        self.org = kwargs.pop('org')
        self.existing = kwargs.pop('object', None)

        super(FolderForm, self).__init__(*args, **kwargs)


class LabelCRUDL(SmartCRUDL):
    model = Label
    actions = ('create', 'create_folder', 'update', 'delete', 'list')

    class List(OrgPermsMixin, SmartListView):
        paginate_by = None

        def derive_queryset(self, **kwargs):
            return Label.label_objects.filter(org=self.request.user.get_org())

        def render_to_response(self, context, **response_kwargs):
            results = [dict(id=l.pk, text=l.name) for l in context['object_list']]
            return HttpResponse(json.dumps(results), content_type='application/javascript')

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ('name', 'folder', 'messages')
        success_url = '@msgs.msg_inbox'
        form_class = LabelForm
        success_message = ''
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super(LabelCRUDL.Create, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create(user.get_org(), user, obj.name, obj.folder)

        def post_save(self, obj, *args, **kwargs):
            obj = super(LabelCRUDL.Create, self).post_save(obj, *args, **kwargs)

            if self.form.cleaned_data['messages']:
                msg_ids = [int(m) for m in self.form.cleaned_data['messages'].split(',') if m.isdigit()]
                messages = Msg.objects.filter(org=obj.org, pk__in=msg_ids)
                if messages:
                    obj.toggle_label(messages, add=True)

            return obj

    class CreateFolder(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ('name',)
        success_url = '@msgs.msg_inbox'
        form_class = FolderForm
        success_message = ''
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super(LabelCRUDL.CreateFolder, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            return kwargs

        def save(self, obj):
            user = self.request.user
            self.object = Label.get_or_create_folder(user.get_org(), user, obj.name)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        success_url = 'id@msgs.msg_filter'
        success_message = ''

        def get_form_kwargs(self):
            kwargs = super(LabelCRUDL.Update, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            kwargs['object'] = self.get_object()
            return kwargs

        def get_form_class(self):
            return FolderForm if self.get_object().is_folder() else LabelForm

        def derive_title(self):
            return _("Update Folder") if self.get_object().is_folder() else _("Update Label")

        def derive_fields(self):
            return ('name',) if self.get_object().is_folder() else ('name', 'folder')

    class Delete(OrgObjPermsMixin, SmartDeleteView):
        redirect_url = "@msgs.msg_inbox"
        cancel_url = "@msgs.msg_inbox"
        success_message = ''


class CallCRUDL(SmartCRUDL):
    model = Call
    actions = ('list',)

    class List(MsgListView):
        fields = ('call_type', 'contact', 'channel', 'time')
        default_order = '-time'
        search_fields = ('contact__urns__path__icontains', 'contact__name__icontains')
        system_label = SystemLabel.TYPE_CALLS

        def get_queryset(self, **kwargs):
            qs = super(CallCRUDL.List, self).get_queryset(**kwargs)
            return qs.order_by('-created_on').select_related('contact')

        def get_contact(self, obj):
            return obj.contact.get_display(self.org)

        def get_context_data(self, *args, **kwargs):
            context = super(CallCRUDL.List, self).get_context_data(*args, **kwargs)
            context['actions'] = []
            return context
