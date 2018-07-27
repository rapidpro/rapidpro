# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import six

from datetime import date, timedelta
from django import forms
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.contrib import messages
from django.forms import Form
from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseRedirect
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.utils.http import urlquote_plus
from django.utils.translation import ugettext_lazy as _
from smartmin.views import SmartCreateView, SmartCRUDL, SmartDeleteView, SmartFormView, SmartListView, SmartReadView, SmartUpdateView
from temba.channels.models import Channel
from temba.contacts.fields import OmniboxField
from temba.contacts.models import ContactGroup, URN, ContactURN, TEL_SCHEME
from temba.formax import FormaxMixin
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.utils import analytics, on_transaction_commit
from temba.utils.expressions import get_function_listing
from temba.utils.views import BaseActionForm
from .models import Broadcast, ExportMessagesTask, Label, Msg, Schedule, SystemLabel
from .tasks import export_messages_task


def send_message_auto_complete_processor(request):
    """
    Adds completions for the expression auto-completion to the request context
    """
    completions = []
    user = request.user
    org = None

    if hasattr(user, 'get_org'):
        org = request.user.get_org()

    if org:
        completions.append(dict(name='contact', display=six.text_type(_("Contact Name"))))
        completions.append(dict(name='contact.first_name', display=six.text_type(_("Contact First Name"))))
        completions.append(dict(name='contact.groups', display=six.text_type(_("Contact Groups"))))
        completions.append(dict(name='contact.language', display=six.text_type(_("Contact Language"))))
        completions.append(dict(name='contact.name', display=six.text_type(_("Contact Name"))))
        completions.append(dict(name='contact.tel', display=six.text_type(_("Contact Phone"))))
        completions.append(dict(name='contact.tel_e164', display=six.text_type(_("Contact Phone - E164"))))
        completions.append(dict(name='contact.uuid', display=six.text_type(_("Contact UUID"))))

        completions.append(dict(name="date", display=six.text_type(_("Current Date and Time"))))
        completions.append(dict(name="date.now", display=six.text_type(_("Current Date and Time"))))
        completions.append(dict(name="date.today", display=six.text_type(_("Current Date"))))
        completions.append(dict(name="date.tomorrow", display=six.text_type(_("Tomorrow's Date"))))
        completions.append(dict(name="date.yesterday", display=six.text_type(_("Yesterday's Date"))))

        for scheme, label in ContactURN.SCHEME_CHOICES:
            if scheme != TEL_SCHEME and scheme in org.get_schemes(Channel.ROLE_SEND):
                completions.append(dict(name="contact.%s" % scheme, display=six.text_type(_("Contact %s" % label))))

        for field in org.contactfields.filter(is_active=True).order_by('label'):
            display = six.text_type(_("Contact Field: %(label)s")) % {'label': field.label}
            completions.append(dict(name="contact.%s" % str(field.key), display=display))

    function_completions = get_function_listing()
    return dict(completions=json.dumps(completions), function_completions=json.dumps(function_completions))


class SendMessageForm(Form):
    omnibox = OmniboxField()
    text = forms.CharField(widget=forms.Textarea, max_length=640)
    schedule = forms.BooleanField(widget=forms.HiddenInput, required=False)
    step_node = forms.CharField(widget=forms.HiddenInput, max_length=36, required=False)

    def __init__(self, user, *args, **kwargs):
        super(SendMessageForm, self).__init__(*args, **kwargs)
        self.user = user
        self.fields['omnibox'].set_user(user)

    def is_valid(self):
        valid = super(SendMessageForm, self).is_valid()
        if valid:
            if ('step_node' not in self.data or not self.data['step_node']) and ('omnibox' not in self.data or len(self.data['omnibox'].strip()) == 0):
                self.errors['__all__'] = self.error_class([six.text_type(_("At least one recipient is required"))])
                return False
        return valid

    def clean(self):
        cleaned = super(SendMessageForm, self).clean()
        if self.user.get_org().is_suspended():
            raise ValidationError(_("Sorry, your account is currently suspended. To enable sending messages, please contact support."))
        return cleaned


class InboxView(OrgPermsMixin, SmartListView):
    """
    Base class for inbox views with message folders and labels listed by the side
    """
    refresh = 10000
    add_button = True
    system_label = None
    fields = ('from', 'message', 'received')
    search_fields = ('text__icontains', 'contact__name__icontains', 'contact__urns__path__icontains')
    paginate_by = 100
    actions = ()
    allow_export = False
    show_channel_logs = False

    def derive_label(self):
        return self.system_label

    def derive_export_url(self):
        redirect = urlquote_plus(self.request.get_full_path())
        label = self.derive_label()
        label_id = label.uuid if isinstance(label, Label) else label
        return '%s?l=%s&redirect=%s' % (reverse('msgs.msg_export'), label_id, redirect)

    def pre_process(self, request, *args, **kwargs):
        if self.system_label:
            org = request.user.get_org()
            self.queryset = SystemLabel.get_queryset(org, self.system_label)

    def get_queryset(self, **kwargs):
        queryset = super(InboxView, self).get_queryset(**kwargs)

        # if we are searching, limit to last 90
        if 'search' in self.request.GET:
            last_90 = timezone.now() - timedelta(days=90)
            queryset = queryset.filter(created_on__gte=last_90)

        return queryset.order_by('-created_on', '-id')

    def get_context_data(self, **kwargs):
        org = self.request.user.get_org()
        counts = SystemLabel.get_counts(org)

        label = self.derive_label()

        # if there isn't a search filtering the queryset, we can replace the count function with a pre-calculated value
        if 'search' not in self.request.GET:
            if isinstance(label, Label) and not label.is_folder():
                self.object_list.count = lambda: label.get_visible_count()
            elif isinstance(label, six.string_types):
                self.object_list.count = lambda: counts[label]

        context = super(InboxView, self).get_context_data(**kwargs)

        folders = [
            dict(count=counts[SystemLabel.TYPE_INBOX], label=_("Inbox"), url=reverse('msgs.msg_inbox')),
            dict(count=counts[SystemLabel.TYPE_FLOWS], label=_("Flows"), url=reverse('msgs.msg_flow')),
            dict(count=counts[SystemLabel.TYPE_ARCHIVED], label=_("Archived"), url=reverse('msgs.msg_archived')),
            dict(count=counts[SystemLabel.TYPE_OUTBOX], label=_("Outbox"), url=reverse('msgs.msg_outbox')),
            dict(count=counts[SystemLabel.TYPE_SENT], label=_("Sent"), url=reverse('msgs.msg_sent')),
            dict(count=counts[SystemLabel.TYPE_CALLS], label=_("Calls"), url=reverse('channels.channelevent_calls')),
            dict(count=counts[SystemLabel.TYPE_SCHEDULED], label=_("Schedules"), url=reverse('msgs.broadcast_schedule_list')),
            dict(count=counts[SystemLabel.TYPE_FAILED], label=_("Failed"), url=reverse('msgs.msg_failed'))
        ]

        context['org'] = org
        context['folders'] = folders
        context['labels'] = Label.get_hierarchy(org)
        context['has_messages'] = any(counts.values())
        context['send_form'] = SendMessageForm(self.request.user)
        context['org_is_purged'] = org.is_purgeable
        context['actions'] = self.actions
        context['current_label'] = label
        context['export_url'] = self.derive_export_url()
        context['show_channel_logs'] = self.show_channel_logs
        return context

    def get_gear_links(self):
        links = []
        if self.allow_export and self.has_org_perm('msgs.msg_export'):
            links.append(dict(title=_('Export'), href='#', js_class="msg-export-btn"))
        return links


class BroadcastForm(forms.ModelForm):
    message = forms.CharField(required=True, widget=forms.Textarea, max_length=160)
    omnibox = OmniboxField()

    def __init__(self, user, *args, **kwargs):
        super(BroadcastForm, self).__init__(*args, **kwargs)
        self.fields['omnibox'].set_user(user)

    def is_valid(self):
        valid = super(BroadcastForm, self).is_valid()
        if valid:
            if 'omnibox' not in self.data or len(self.data['omnibox'].strip()) == 0:  # pragma: needs cover
                self.errors['__all__'] = self.error_class([_("At least one recipient is required")])
                return False

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
        field_config = {'restrict': {'label': ''}, 'omnibox': {'label': ''}, 'message': {'label': '', 'help': ''}}
        success_message = ''
        success_url = 'msgs.broadcast_schedule_list'

        def get_form_kwargs(self):
            args = super(BroadcastCRUDL.Update, self).get_form_kwargs()
            args['user'] = self.request.user
            return args

        def derive_initial(self):
            selected = ['g-%s' % _.uuid for _ in self.object.groups.all()]
            selected += ['c-%s' % _.uuid for _ in self.object.contacts.all()]
            selected = ','.join(selected)
            message = self.object.text[self.object.base_language]
            return dict(message=message, omnibox=selected)

        def save(self, *args, **kwargs):
            form = self.form
            broadcast = self.object

            # save off our broadcast info
            omnibox = form.cleaned_data['omnibox']

            # set our new message
            broadcast.text = {broadcast.base_language: form.cleaned_data['message']}
            broadcast.update_recipients(list(omnibox['groups']) + list(omnibox['contacts']) + list(omnibox['urns']))

            broadcast.save()
            return broadcast

    class ScheduleList(InboxView):
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
        fields = ('omnibox', 'text', 'schedule', 'step_node')
        success_url = '@msgs.msg_inbox'
        submit_button_name = _('Send')

        def get_context_data(self, **kwargs):
            context = super(BroadcastCRUDL.Send, self).get_context_data(**kwargs)
            return context

        def pre_process(self, *args, **kwargs):
            response = super(BroadcastCRUDL.Send, self).pre_process(*args, **kwargs)
            org = self.request.user.get_org()
            simulation = self.request.GET.get('simulation', 'false') == 'true'

            if simulation:
                return response

            # can this org send to any URN schemes?
            if not org.get_schemes(Channel.ROLE_SEND):
                return HttpResponseBadRequest(_("You must add a phone number before sending messages"))

            return response

        def derive_success_message(self):
            if 'from_contact' not in self.request.POST:
                return super(BroadcastCRUDL.Send, self).derive_success_message()
            else:
                return None

        def get_success_url(self):
            success_url = super(BroadcastCRUDL.Send, self).get_success_url()
            if 'from_contact' in self.request.POST:
                contact = self.form.cleaned_data['omnibox']['contacts'][0]
                success_url = reverse('contacts.contact_read', args=[contact.uuid])
            return success_url

        def form_invalid(self, form):
            if '_format' in self.request.GET and self.request.GET['_format'] == 'json':
                return HttpResponse(json.dumps(dict(status="error", errors=form.errors)), content_type='application/json', status=400)
            else:
                return super(BroadcastCRUDL.Send, self).form_invalid(form)

        def form_valid(self, form):
            self.form = form
            user = self.request.user
            org = user.get_org()
            simulation = self.request.GET.get('simulation', 'false') == 'true'

            omnibox = self.form.cleaned_data['omnibox']
            has_schedule = self.form.cleaned_data['schedule']
            step_uuid = self.form.cleaned_data.get('step_node', None)
            text = self.form.cleaned_data['text']

            groups = list(omnibox['groups'])
            contacts = list(omnibox['contacts'])
            urns = list(omnibox['urns'])
            recipients = list()

            if step_uuid:
                from .tasks import send_to_flow_node
                get_params = {k: v for k, v in self.request.GET.items()}
                get_params.update({'s': step_uuid})
                send_to_flow_node.delay(org.pk, user.pk, text, **get_params)
                if '_format' in self.request.GET and self.request.GET['_format'] == 'json':
                    return HttpResponse(json.dumps(dict(status="success")), content_type='application/json')
                else:
                    return HttpResponseRedirect(self.get_success_url())

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
            broadcast = Broadcast.create(org, user, text, recipients,
                                         schedule=schedule)

            if not has_schedule:
                self.post_save(broadcast)
                super(BroadcastCRUDL.Send, self).form_valid(form)

            analytics.track(self.request.user.username, 'temba.broadcast_created',
                            dict(contacts=len(contacts), groups=len(groups), urns=len(urns)))

            if '_format' in self.request.GET and self.request.GET['_format'] == 'json':
                data = dict(status="success", redirect=reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
                return HttpResponse(json.dumps(data), content_type='application/json')
            else:
                if self.form.cleaned_data['schedule']:
                    return HttpResponseRedirect(reverse('msgs.broadcast_schedule_read', args=[broadcast.pk]))
                return HttpResponseRedirect(self.get_success_url())

        def post_save(self, obj):
            # fire our send in celery
            from temba.msgs.tasks import send_broadcast_task
            on_transaction_commit(lambda: send_broadcast_task.delay(obj.pk))
            return obj

        def get_form_kwargs(self):
            kwargs = super(BroadcastCRUDL.Send, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs


class MsgActionForm(BaseActionForm):
    allowed_actions = (('label', _("Label Messages")),
                       ('archive', _("Archive Messages")),
                       ('restore', _("Move to Inbox")),
                       ('resend', _("Resend Messages")),
                       ('delete', _("Delete Messages")))

    model = Msg
    label_model = Label
    label_model_manager = 'label_objects'
    has_is_active = False

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
            if response and 'error' in response:  # pragma: no cover
                return HttpResponse(json.dumps(response), content_type='application/json', status=400)

        return self.get(request, *args, **kwargs)


class TestMessageForm(forms.Form):
    channel = forms.ModelChoiceField(Channel.objects.filter(id__lt=0),
                                     help_text=_("Which channel will deliver the message"))
    urn = forms.CharField(max_length=14,
                          help_text=_("The URN of the contact delivering this message"))
    text = forms.CharField(max_length=160, widget=forms.Textarea,
                           help_text=_("The message that is being delivered"))

    def __init__(self, *args, **kwargs):  # pragma: needs cover
        org = kwargs['org']
        del kwargs['org']

        super(TestMessageForm, self).__init__(*args, **kwargs)
        self.fields['channel'].queryset = Channel.objects.filter(org=org, is_active=True)


class ExportForm(Form):
    LABEL_CHOICES = ((0, _("Just this label")), (1, _("All messages")))
    SYSTEM_LABEL_CHOICES = ((0, _("Just this folder")), (1, _("All messages")))

    export_all = forms.ChoiceField(choices=(), label=_("Selection"), initial=0)

    groups = forms.ModelMultipleChoiceField(queryset=ContactGroup.user_groups.none(),
                                            required=False, label=_("Groups"))
    start_date = forms.DateField(required=False,
                                 help_text=_("The date for the oldest message to export. "
                                             "(Leave blank to export from the oldest message)."))
    end_date = forms.DateField(required=False,
                               help_text=_("The date for the latest message to export. "
                                           "(Leave blank to export up to the latest message)."))

    def __init__(self, user, label, *args, **kwargs):
        super(ExportForm, self).__init__(*args, **kwargs)
        self.user = user

        self.fields['export_all'].choices = self.LABEL_CHOICES if label else self.SYSTEM_LABEL_CHOICES

        self.fields['groups'].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)
        self.fields['groups'].help_text = _("Export only messages from these contact groups. "
                                            "(Leave blank to export all messages).")

    def clean(self):
        cleaned_data = super(ExportForm, self).clean()
        start_date = cleaned_data.get('start_date')
        end_date = cleaned_data.get('end_date')

        if start_date and start_date > date.today():  # pragma: needs cover
            raise forms.ValidationError(_("Start date can't be in the future."))

        if end_date and start_date and end_date < start_date:  # pragma: needs cover
            raise forms.ValidationError(_("End date can't be before start date"))

        return cleaned_data


class MsgCRUDL(SmartCRUDL):
    model = Msg
    actions = ('inbox', 'flow', 'archived', 'outbox', 'sent', 'failed', 'filter', 'test', 'export')

    class Export(ModalMixin, OrgPermsMixin, SmartFormView):

        form_class = ExportForm
        submit_button_name = "Export"
        success_url = "@msgs.msg_inbox"

        def derive_label(self):
            # label is either a UUID of a Label instance (36 chars) or a system label type code (1 char)
            label_id = self.request.GET['l']
            if len(label_id) == 1:
                return label_id, None
            else:
                return None, Label.all_objects.get(org=self.request.user.get_org(), uuid=label_id)

        def get_success_url(self):
            return self.request.GET.get('redirect') or reverse('msgs.msg_inbox')

        def form_invalid(self, form):  # pragma: needs cover
            if '_format' in self.request.GET and self.request.GET['_format'] == 'json':
                return HttpResponse(json.dumps(dict(status="error", errors=form.errors)), content_type='application/json', status=400)
            else:
                return super(MsgCRUDL.Export, self).form_invalid(form)

        def form_valid(self, form):
            user = self.request.user
            org = user.get_org()

            export_all = bool(int(form.cleaned_data['export_all']))
            groups = form.cleaned_data['groups']
            start_date = form.cleaned_data['start_date']
            end_date = form.cleaned_data['end_date']

            system_label, label = (None, None) if export_all else self.derive_label()

            # is there already an export taking place?
            existing = ExportMessagesTask.get_recent_unfinished(org)
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))

            # otherwise, off we go
            else:
                export = ExportMessagesTask.create(org, user, system_label=system_label, label=label,
                                                   groups=groups, start_date=start_date, end_date=end_date)

                on_transaction_commit(lambda: export_messages_task.delay(export.id))

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):  # pragma: needs cover
                    messages.info(self.request, _("We are preparing your export. We will e-mail you at %s when "
                                                  "it is ready.") % self.request.user.username)

                else:
                    dl_url = reverse('assets.download', kwargs=dict(type='message_export', pk=export.pk))
                    messages.info(self.request, _("Export complete, you can find it here: %s (production users "
                                                  "will get an email)") % dl_url)

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

        def get_form_kwargs(self):
            kwargs = super(MsgCRUDL.Export, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            kwargs['label'] = self.derive_label()[1]
            return kwargs

    class Test(SmartFormView):
        form_class = TestMessageForm
        fields = ('channel', 'urn', 'text')
        title = "Test Message Delivery"
        permissions = 'msgs.msg_test'

        def form_valid(self, *args, **kwargs):  # pragma: no cover
            data = self.form.cleaned_data
            handled = Msg.create_incoming(data['channel'], URN.from_tel(data['urn']), data['text'],
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
            return self.render_to_response(context)

        def get_form_kwargs(self, *args, **kwargs):  # pragma: needs cover
            kwargs = super(MsgCRUDL.Test, self).get_form_kwargs(*args, **kwargs)
            kwargs['org'] = self.request.user.get_org()
            return kwargs

    class Inbox(MsgActionMixin, InboxView):
        title = _("Inbox")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_INBOX
        actions = ['archive', 'label']
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Inbox, self).get_queryset(**kwargs)
            return qs.prefetch_related('labels').select_related('contact')

    class Flow(MsgActionMixin, InboxView):
        title = _("Flow Messages")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_FLOWS
        actions = ['label']
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Flow, self).get_queryset(**kwargs)
            return qs.prefetch_related('labels', 'steps__run__flow').select_related('contact')

    class Archived(MsgActionMixin, InboxView):
        title = _("Archived")
        template_name = 'msgs/msg_archived.haml'
        system_label = SystemLabel.TYPE_ARCHIVED
        actions = ['restore', 'label', 'delete']
        allow_export = True

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Archived, self).get_queryset(**kwargs)
            return qs.prefetch_related('labels', 'steps__run__flow').select_related('contact')

    class Outbox(MsgActionMixin, InboxView):
        title = _("Outbox Messages")
        template_name = 'msgs/message_box.haml'
        system_label = SystemLabel.TYPE_OUTBOX
        actions = ()
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Outbox, self).get_queryset(**kwargs)
            return qs.prefetch_related('channel_logs', 'steps__run__flow').select_related('contact')

    class Sent(MsgActionMixin, InboxView):
        title = _("Sent Messages")
        template_name = 'msgs/msg_sent.haml'
        system_label = SystemLabel.TYPE_SENT
        actions = ()
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):  # pragma: needs cover
            qs = super(MsgCRUDL.Sent, self).get_queryset(**kwargs)
            return qs.prefetch_related('channel_logs', 'steps__run__flow').select_related('contact')

    class Failed(MsgActionMixin, InboxView):
        title = _("Failed Outgoing Messages")
        template_name = 'msgs/msg_failed.haml'
        success_message = ''
        system_label = SystemLabel.TYPE_FAILED
        actions = ['resend']
        allow_export = True
        show_channel_logs = True

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Failed, self).get_queryset(**kwargs)
            return qs.prefetch_related('channel_logs', 'steps__run__flow').select_related('contact')

    class Filter(MsgActionMixin, InboxView):
        template_name = 'msgs/msg_filter.haml'
        actions = ['unlabel', 'label']

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def get_gear_links(self):
            links = []

            edit_btn_cls = 'folder-update-btn' if self.derive_label().is_folder() else 'label-update-btn'

            if self.has_org_perm('msgs.msg_update'):
                links.append(dict(title=_('Edit'), href='#', js_class=edit_btn_cls))

            if self.has_org_perm('msgs.msg_export'):
                links.append(dict(title=_('Export'), href='#', js_class="msg-export-btn"))

            if self.has_org_perm('msgs.broadcast_send'):
                links.append(dict(title=_('Send All'), style='btn-primary', href="#",
                                  js_class='filter-send-all-send-button'))

            if self.has_org_perm('msgs.label_delete'):
                links.append(dict(title=_('Remove'), href="#", js_class='remove-label'))

            return links

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<label_id>\d+)/$' % (path, action)

        def derive_label(self):
            return Label.all_objects.get(org=self.request.user.get_org(), id=self.kwargs['label_id'])

        def get_queryset(self, **kwargs):
            qs = super(MsgCRUDL.Filter, self).get_queryset(**kwargs)
            qs = self.derive_label().filter_messages(qs).filter(visibility=Msg.VISIBILITY_VISIBLE)

            return qs.prefetch_related('labels', 'steps__run__flow').select_related('contact')


class BaseLabelForm(forms.ModelForm):
    def clean_name(self):
        name = self.cleaned_data['name']

        if not Label.is_valid_name(name):
            raise forms.ValidationError(_("Name must not be blank or begin with punctuation"))

        existing_id = self.existing.pk if self.existing else None
        if Label.all_objects.filter(org=self.org, name__iexact=name).exclude(pk=existing_id).exists():
            raise forms.ValidationError(_("Name must be unique"))

        labels_count = Label.all_objects.filter(org=self.org, is_active=True).count()
        if labels_count >= Label.MAX_ORG_LABELS:
            raise forms.ValidationError(_("This org has %s labels and the limit is %s. "
                                          "You must delete existing ones before you can "
                                          "create new ones." % (labels_count, Label.MAX_ORG_LABELS)))

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
        default_order = ('name',)

        def derive_queryset(self, **kwargs):
            return Label.label_objects.filter(org=self.request.user.get_org())

        def render_to_response(self, context, **response_kwargs):
            results = [dict(id=l.uuid, text=l.name) for l in context['object_list']]
            return HttpResponse(json.dumps(results), content_type='application/json')

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

            if self.form.cleaned_data['messages']:  # pragma: needs cover
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

        def post(self, request, *args, **kwargs):
            label = self.get_object()
            label.release()

            return HttpResponseRedirect(self.get_redirect_url())
