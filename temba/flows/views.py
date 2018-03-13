# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import json
import logging
import regex
import six
import time
import traceback

from random import randint
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.paginator import Paginator
from django.core.urlresolvers import reverse
from django.db.models import Count, Min, Max, Sum, QuerySet
from django import forms
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.utils import timezone
from django.utils.encoding import force_text
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView
from functools import cmp_to_key
from itertools import chain
from smartmin.views import SmartCRUDL, SmartCreateView, SmartReadView, SmartListView, SmartUpdateView, smart_url
from smartmin.views import SmartDeleteView, SmartTemplateView, SmartFormView
from temba.channels.models import Channel
from temba.contacts.fields import OmniboxField
from temba.contacts.models import Contact, ContactField, TEL_SCHEME, ContactURN, ContactGroup
from temba.ivr.models import IVRCall
from temba.ussd.models import USSDSession
from temba.orgs.models import Org
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.flows.models import Flow, FlowRun, FlowRevision, FlowRunCount
from temba.flows.tasks import export_flow_results_task
from temba.msgs.models import Msg, Label, PENDING
from temba.triggers.models import Trigger
from temba.utils import analytics, on_transaction_commit, chunk_list, goflow
from temba.utils.dates import datetime_to_str
from temba.utils.expressions import get_function_listing
from temba.utils.goflow import get_client
from temba.utils.views import BaseActionForm
from uuid import uuid4
from .models import FlowStep, RuleSet, ActionLog, ExportFlowResultsTask, FlowLabel, FlowPathRecentRun
from .models import FlowUserConflictException, FlowVersionConflictException, FlowInvalidCycleException

logger = logging.getLogger(__name__)


EXPIRES_CHOICES = (
    (0, _('Never')),
    (5, _('After 5 minutes')),
    (10, _('After 10 minutes')),
    (15, _('After 15 minutes')),
    (30, _('After 30 minutes')),
    (60, _('After 1 hour')),
    (60 * 3, _('After 3 hours')),
    (60 * 6, _('After 6 hours')),
    (60 * 12, _('After 12 hours')),
    (60 * 24, _('After 1 day')),
    (60 * 24 * 3, _('After 3 days')),
    (60 * 24 * 7, _('After 1 week')),
    (60 * 24 * 14, _('After 2 weeks')),
    (60 * 24 * 30, _('After 30 days'))
)


IVR_EXPIRES_CHOICES = (
    (1, _('After 1 minute')),
    (2, _('After 2 minutes')),
    (3, _('After 3 minutes')),
    (4, _('After 4 minutes')),
    (5, _('After 5 minutes')),
    (10, _('After 10 minutes')),
    (15, _('After 15 minutes'))
)


class BaseFlowForm(forms.ModelForm):
    def clean_keyword_triggers(self):
        org = self.user.get_org()
        value = self.cleaned_data.get('keyword_triggers', '')

        duplicates = []
        wrong_format = []
        cleaned_keywords = []

        for keyword in value.split(','):
            keyword = keyword.lower().strip()
            if not keyword:
                continue

            if not regex.match('^\w+$', keyword, flags=regex.UNICODE | regex.V0) or len(keyword) > Trigger.KEYWORD_MAX_LEN:
                wrong_format.append(keyword)

            # make sure it is unique on this org
            existing = Trigger.objects.filter(org=org, keyword__iexact=keyword, is_archived=False, is_active=True)
            if self.instance:
                existing = existing.exclude(flow=self.instance.pk)

            if existing:
                duplicates.append(keyword)
            else:
                cleaned_keywords.append(keyword)

        if wrong_format:
            raise forms.ValidationError(_('"%s" must be a single word, less than %d characters, containing only letter '
                                          'and numbers') % (', '.join(wrong_format), Trigger.KEYWORD_MAX_LEN))

        if duplicates:
            if len(duplicates) > 1:
                error_message = _('The keywords "%s" are already used for another flow') % ', '.join(duplicates)
            else:
                error_message = _('The keyword "%s" is already used for another flow') % ', '.join(duplicates)
            raise forms.ValidationError(error_message)

        return ','.join(cleaned_keywords)

    class Meta:
        model = Flow
        fields = '__all__'


class FlowActionForm(BaseActionForm):
    allowed_actions = (('archive', _("Archive Flows")),
                       ('label', _("Label Messages")),
                       ('restore', _("Restore Flows")))

    model = Flow
    label_model = FlowLabel
    has_is_active = True

    class Meta:
        fields = ('action', 'objects', 'label', 'add')


class FlowActionMixin(SmartListView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(FlowActionMixin, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        org = user.get_org()

        form = FlowActionForm(self.request.POST, org=org, user=user)

        toast = None
        ignored = []
        if form.is_valid():
            changed = form.execute().get('changed')
            for flow in form.cleaned_data['objects']:
                if flow.id not in changed:
                    ignored.append(flow.name)

            if form.cleaned_data['action'] == 'archive' and ignored:
                if len(ignored) > 1:
                    toast = _('%s are used inside a campaign. To archive them, first remove them from your campaigns.' % ' and '.join(ignored))
                else:
                    toast = _('%s is used inside a campaign. To archive it, first remove it from your campaigns.' % ignored[0])

        response = self.get(request, *args, **kwargs)

        if toast:
            response['Temba-Toast'] = toast

        return response


def msg_log_cmp(a, b):
    if a.__class__ == b.__class__:
        return a.pk - b.pk
    else:
        if a.created_on == b.created_on:  # pragma: needs cover
            return 0
        elif a.created_on < b.created_on:
            return -1
        else:
            return 1


class PartialTemplate(SmartTemplateView):  # pragma: no cover

    def pre_process(self, request, *args, **kwargs):
        self.template = kwargs['template']
        return

    def get_template_names(self):
        return "partials/%s.html" % self.template


class FlowRunCRUDL(SmartCRUDL):
    actions = ('delete',)
    model = FlowRun

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ('pk',)
        success_message = None

        def post(self, request, *args, **kwargs):
            self.get_object().release()
            return HttpResponse()


class FlowCRUDL(SmartCRUDL):
    actions = ('list', 'archived', 'copy', 'create', 'delete', 'update', 'simulate', 'export_results',
               'upload_action_recording', 'editor', 'results', 'run_table', 'category_counts', 'json',
               'broadcast', 'activity', 'activity_chart', 'filter', 'campaign', 'completion', 'revisions',
               'recent_messages', 'assets', 'upload_media_action')

    model = Flow

    class RecentMessages(OrgObjPermsMixin, SmartReadView):
        def get(self, request, *args, **kwargs):
            flow = self.get_object()

            exit_uuids = request.GET.get('exits', '').split(',')
            to_uuid = request.GET.get('to')

            recent_messages = []

            if exit_uuids and to_uuid:
                for recent_run in FlowPathRecentRun.get_recent(exit_uuids, to_uuid):
                    recent_messages.append({
                        'sent': datetime_to_str(recent_run['visited_on'], tz=flow.org.timezone),
                        'text': recent_run['text']
                    })

            return JsonResponse(recent_messages, safe=False)

    class Revisions(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            flow = self.get_object()

            revision_id = request.GET.get('definition', None)

            if revision_id:
                revision = FlowRevision.objects.get(flow=flow, pk=revision_id)
                return JsonResponse(revision.get_definition_json())
            else:
                revisions = []
                for revision in flow.revisions.all().order_by('-created_on')[:25]:
                    # validate the flow definition before presenting it to the user
                    try:
                        FlowRevision.validate_flow_definition(revision.get_definition_json())
                        revisions.append(revision.as_json())

                    except ValueError:
                        # "expected" error in the def, silently cull it
                        pass

                    except Exception:
                        # something else, we still cull, but report it to sentry
                        logger.exception("Error validating flow revision: %s [%d]" % (flow.uuid, revision.id))
                        pass

                return JsonResponse(revisions, safe=False)

    class OrgQuerysetMixin(object):
        def derive_queryset(self, *args, **kwargs):
            queryset = super(FlowCRUDL.OrgQuerysetMixin, self).derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated():  # pragma: needs cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        class FlowCreateForm(BaseFlowForm):
            keyword_triggers = forms.CharField(required=False, label=_("Global keyword triggers"),
                                               help_text=_("When a user sends any of these keywords they will begin this flow"))

            flow_type = forms.ChoiceField(label=_('Run flow over'),
                                          help_text=_('Send messages, place phone calls, or submit Surveyor runs'),
                                          choices=((Flow.FLOW, 'Messaging'),
                                                   (Flow.USSD, 'USSD Messaging'),
                                                   (Flow.VOICE, 'Phone Call'),
                                                   (Flow.SURVEY, 'Surveyor')))

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.Create.FlowCreateForm, self).__init__(*args, **kwargs)
                self.user = user

                org_languages = self.user.get_org().languages.all().order_by('orgs', 'name')
                language_choices = ((lang.iso_code, lang.name) for lang in org_languages)
                self.fields['base_language'] = forms.ChoiceField(label=_('Language'),
                                                                 initial=self.user.get_org().primary_language,
                                                                 choices=language_choices)

            class Meta:
                model = Flow
                fields = ('name', 'keyword_triggers', 'flow_type', 'base_language')

        form_class = FlowCreateForm
        success_url = 'uuid@flows.flow_editor'
        success_message = ''
        field_config = dict(name=dict(help=_("Choose a name to describe this flow, e.g. Demographic Survey")))

        def derive_exclude(self):
            org = self.request.user.get_org()
            exclude = []

            if not org.primary_language:
                exclude.append('base_language')

            return exclude

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super(FlowCRUDL.Create, self).get_context_data(**kwargs)
            context['has_flows'] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count() > 0
            return context

        def save(self, obj):
            analytics.track(self.request.user.username, 'temba.flow_created', dict(name=obj.name))
            org = self.request.user.get_org()

            if not obj.flow_type:  # pragma: needs cover
                obj.flow_type = Flow.FLOW

            # if we don't have a language, use base
            if not obj.base_language:  # pragma: needs cover
                obj.base_language = 'base'

            # default expiration is a week
            expires_after_minutes = 60 * 24 * 7
            if obj.flow_type == Flow.VOICE:
                # ivr expires after 5 minutes of inactivity
                expires_after_minutes = 5

            self.object = Flow.create(org, self.request.user, obj.name,
                                      flow_type=obj.flow_type, expires_after_minutes=expires_after_minutes,
                                      base_language=obj.base_language)

        def post_save(self, obj):
            user = self.request.user
            org = user.get_org()

            # create triggers for this flow only if there are keywords and we aren't a survey
            if self.form.cleaned_data.get('flow_type') != Flow.SURVEY:
                if len(self.form.cleaned_data['keyword_triggers']) > 0:
                    for keyword in self.form.cleaned_data['keyword_triggers'].split(','):
                        Trigger.objects.create(org=org, keyword=keyword, flow=obj, created_by=user, modified_by=user)

            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ('id',)
        cancel_url = 'uuid@flows.flow_editor'
        success_message = ''

        def get_success_url(self):
            return reverse("flows.flow_list")

        def post(self, request, *args, **kwargs):
            flow = self.get_object()
            self.object = flow

            flows = Flow.objects.filter(org=flow.org, flow_dependencies__in=[flow])
            if flows.count():
                return HttpResponseRedirect(smart_url(self.cancel_url, flow))

            # do the actual deletion
            flow.release()

            # we can't just redirect so as to make our modal do the right thing
            response = self.render_to_response(self.get_context_data(success_url=self.get_success_url(),
                                                                     success_script=getattr(self, 'success_script', None)))
            response['Temba-Success'] = self.get_success_url()

            return response

    class Copy(OrgObjPermsMixin, SmartUpdateView):
        fields = []
        success_message = ''

        def form_valid(self, form):
            # copy our current object
            copy = Flow.copy(self.object, self.request.user)

            # redirect to the newly created flow
            return HttpResponseRedirect(reverse('flows.flow_editor', args=[copy.uuid]))

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class FlowUpdateForm(BaseFlowForm):

            expires_after_minutes = forms.ChoiceField(label=_('Expire inactive contacts'),
                                                      help_text=_(
                                                          "When inactive contacts should be removed from the flow"),
                                                      initial=str(60 * 24 * 7),
                                                      choices=EXPIRES_CHOICES)

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.Update.FlowUpdateForm, self).__init__(*args, **kwargs)
                self.user = user

                metadata = self.instance.metadata
                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org, flow=self.instance, is_archived=False, groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD
                ).order_by('created_on')

                if self.instance.flow_type == Flow.VOICE:
                    expiration = self.fields['expires_after_minutes']
                    expiration.choices = IVR_EXPIRES_CHOICES
                    expiration.initial = 5

                # if we don't have a base language let them pick one (this is immutable)
                if not self.instance.base_language:
                    choices = [('', 'No Preference')]
                    choices += [(lang.iso_code, lang.name) for lang in self.instance.org.languages.all().order_by('orgs', 'name')]
                    self.fields['base_language'] = forms.ChoiceField(label=_('Language'), choices=choices)

                if self.instance.flow_type == Flow.SURVEY:
                    contact_creation = forms.ChoiceField(
                        label=_('Create a contact '),
                        initial=metadata.get(Flow.CONTACT_CREATION, Flow.CONTACT_PER_RUN),
                        help_text=_("Whether surveyor logins should be used as the contact for each run"),
                        choices=(
                            (Flow.CONTACT_PER_RUN, _('For each run')),
                            (Flow.CONTACT_PER_LOGIN, _('For each login'))
                        )
                    )

                    self.fields[Flow.CONTACT_CREATION] = contact_creation
                else:
                    self.fields['keyword_triggers'] = forms.CharField(required=False,
                                                                      label=_("Global keyword triggers"),
                                                                      help_text=_("When a user sends any of these keywords they will begin this flow"),
                                                                      initial=','.join([t.keyword for t in flow_triggers]))

            class Meta:
                model = Flow
                fields = ('name', 'labels', 'base_language', 'expires_after_minutes', 'ignore_triggers')

        success_message = ''
        fields = ('name', 'expires_after_minutes')
        form_class = FlowUpdateForm

        def derive_fields(self):
            fields = [field for field in self.fields]

            obj = self.get_object()
            if not obj.base_language and self.org.primary_language:  # pragma: needs cover
                fields += ['base_language']

            if obj.flow_type == Flow.SURVEY:
                fields.insert(len(fields) - 1, Flow.CONTACT_CREATION)
            else:
                fields.insert(1, 'keyword_triggers')
                fields.append('ignore_triggers')

            return fields

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def pre_save(self, obj):
            obj = super(FlowCRUDL.Update, self).pre_save(obj)
            metadata = obj.metadata

            if Flow.CONTACT_CREATION in self.form.cleaned_data:
                metadata[Flow.CONTACT_CREATION] = self.form.cleaned_data[Flow.CONTACT_CREATION]
            obj.metadata = metadata
            return obj

        def post_save(self, obj):
            keywords = set()
            user = self.request.user
            org = user.get_org()

            if 'keyword_triggers' in self.form.cleaned_data:

                existing_keywords = set(t.keyword for t in obj.triggers.filter(org=org, flow=obj,
                                                                               trigger_type=Trigger.TYPE_KEYWORD,
                                                                               is_archived=False, groups=None))

                if len(self.form.cleaned_data['keyword_triggers']) > 0:
                    keywords = set(self.form.cleaned_data['keyword_triggers'].split(','))

                removed_keywords = existing_keywords.difference(keywords)
                for keyword in removed_keywords:
                    obj.triggers.filter(org=org, flow=obj, keyword=keyword,
                                        groups=None, is_archived=False).update(is_archived=True)

                added_keywords = keywords.difference(existing_keywords)
                archived_keywords = [t.keyword for t in obj.triggers.filter(org=org, flow=obj, trigger_type=Trigger.TYPE_KEYWORD,
                                                                            is_archived=True, groups=None)]
                for keyword in added_keywords:
                    # first check if the added keyword is not amongst archived
                    if keyword in archived_keywords:  # pragma: needs cover
                        obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None).update(is_archived=False)
                    else:
                        Trigger.objects.create(org=org, keyword=keyword, trigger_type=Trigger.TYPE_KEYWORD,
                                               flow=obj, created_by=user, modified_by=user)

            # run async task to update all runs
            from .tasks import update_run_expirations_task
            on_transaction_commit(lambda: update_run_expirations_task.delay(obj.pk))

            return obj

    class UploadActionRecording(OrgPermsMixin, SmartUpdateView):
        def post(self, request, *args, **kwargs):  # pragma: needs cover
            path = self.save_recording_upload(self.request.FILES['file'], self.request.POST.get('actionset'), self.request.POST.get('action'))
            return JsonResponse(dict(path=path))

        def save_recording_upload(self, file, actionset_id, action_uuid):  # pragma: needs cover
            flow = self.get_object()
            return default_storage.save('recordings/%d/%d/steps/%s.wav' % (flow.org.pk, flow.id, action_uuid), file)

    class UploadMediaAction(OrgPermsMixin, SmartUpdateView):
        def post(self, request, *args, **kwargs):
            generated_uuid = six.text_type(uuid4())
            path = self.save_media_upload(self.request.FILES['file'], self.request.POST.get('actionset'),
                                          generated_uuid)
            return JsonResponse(dict(path=path))

        def save_media_upload(self, file, actionset_id, name_uuid):
            flow = self.get_object()
            extension = file.name.split('.')[-1]
            return default_storage.save('attachments/%d/%d/steps/%s.%s' % (flow.org.pk, flow.id, name_uuid, extension),
                                        file)

    class BaseList(FlowActionMixin, OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        title = _("Flows")
        refresh = 10000
        fields = ('name', 'modified_on')
        default_template = 'flows/flow_list.html'
        default_order = ('-saved_on',)
        search_fields = ('name__icontains',)

        def get_context_data(self, **kwargs):
            context = super(FlowCRUDL.BaseList, self).get_context_data(**kwargs)
            context['org_has_flows'] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count()
            context['folders'] = self.get_folders()
            context['labels'] = self.get_flow_labels()
            context['campaigns'] = self.get_campaigns()
            context['request_url'] = self.request.path
            context['actions'] = self.actions

            # decorate flow objects with their run activity stats
            for flow in context['object_list']:
                flow.run_stats = flow.get_run_stats()

            return context

        def derive_queryset(self, *args, **kwargs):
            qs = super(FlowCRUDL.BaseList, self).derive_queryset(*args, **kwargs)
            return qs.exclude(flow_type=Flow.MESSAGE).exclude(is_active=False)

        def get_campaigns(self):
            from temba.campaigns.models import CampaignEvent
            org = self.request.user.get_org()
            events = CampaignEvent.objects.filter(campaign__org=org, is_active=True, campaign__is_active=True,
                                                  flow__is_archived=False, flow__is_active=True, flow__flow_type=Flow.FLOW)
            return events.values('campaign__name', 'campaign__id').annotate(count=Count('id')).order_by('campaign__name')

        def get_flow_labels(self):
            labels = []
            for label in FlowLabel.objects.filter(org=self.request.user.get_org(), parent=None):
                labels.append(dict(pk=label.pk, label=label.name, count=label.get_flows_count(), children=label.children.all()))
            return labels

        def get_folders(self):
            org = self.request.user.get_org()

            return [
                dict(label="Active", url=reverse('flows.flow_list'),
                     count=Flow.objects.exclude(flow_type=Flow.MESSAGE).filter(is_active=True,
                                                                               is_archived=False,
                                                                               org=org).count()),
                dict(label="Archived", url=reverse('flows.flow_archived'),
                     count=Flow.objects.exclude(flow_type=Flow.MESSAGE).filter(is_active=True,
                                                                               is_archived=True,
                                                                               org=org).count())
            ]

    class Archived(BaseList):
        actions = ('restore',)
        default_order = ('-created_on',)

        def derive_queryset(self, *args, **kwargs):
            return super(FlowCRUDL.Archived, self).derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Flows")
        actions = ('archive', 'label')

        def derive_queryset(self, *args, **kwargs):
            queryset = super(FlowCRUDL.List, self).derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            types = self.request.GET.getlist('flow_type')
            if types:
                queryset = queryset.filter(flow_type__in=types)
            return queryset

    class Campaign(BaseList):
        actions = ['label']
        campaign = None

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<campaign_id>\d+)/$' % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.get_campaign().name

        def get_campaign(self):
            if not self.campaign:
                from temba.campaigns.models import Campaign
                campaign_id = self.kwargs['campaign_id']
                self.campaign = Campaign.objects.filter(id=campaign_id).first()
            return self.campaign

        def get_queryset(self, **kwargs):
            from temba.campaigns.models import CampaignEvent
            flow_ids = CampaignEvent.objects.filter(campaign=self.get_campaign(),
                                                    flow__is_archived=False,
                                                    flow__flow_type=Flow.FLOW).values('flow__id')

            flows = Flow.objects.filter(id__in=flow_ids).order_by('-modified_on')
            return flows

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Campaign, self).get_context_data(*args, **kwargs)
            context['current_campaign'] = self.get_campaign()
            return context

    class Filter(BaseList):
        add_button = True
        actions = ['unlabel', 'label']

        def get_gear_links(self):
            links = []

            if self.has_org_perm('flows.flow_update'):
                links.append(dict(title=_('Edit'),
                                  href='#',
                                  js_class="label-update-btn"))

            if self.has_org_perm('flows.flow_delete'):
                links.append(dict(title=_('Remove'), href="#", js_class='remove-label'))

            return links

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Filter, self).get_context_data(*args, **kwargs)
            context['current_label'] = self.derive_label()
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<label_id>\d+)/$' % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.derive_label().name

        def derive_label(self):
            return FlowLabel.objects.get(pk=self.kwargs['label_id'])

        def get_label_filter(self):
            label = FlowLabel.objects.get(pk=self.kwargs['label_id'])
            children = label.children.all()
            if children:  # pragma: needs cover
                return [l for l in FlowLabel.objects.filter(parent=label)] + [label]
            else:
                return [label]

        def get_queryset(self, **kwargs):
            qs = super(FlowCRUDL.Filter, self).get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org()).order_by('-created_on')
            qs = qs.filter(labels__in=self.get_label_filter(), is_archived=False).distinct()

            return qs

    class Completion(OrgPermsMixin, SmartListView):
        def render_to_response(self, context, **response_kwargs):

            org = self.request.user.get_org()

            contact_variables = [
                dict(name='contact', display=six.text_type(_('Contact Name'))),
                dict(name='contact.first_name', display=six.text_type(_('Contact First Name'))),
                dict(name='contact.groups', display=six.text_type(_('Contact Groups'))),
                dict(name='contact.language', display=six.text_type(_('Contact Language'))),
                dict(name='contact.mailto', display=six.text_type(_('Contact Email Address'))),
                dict(name='contact.name', display=six.text_type(_('Contact Name'))),
                dict(name='contact.tel', display=six.text_type(_('Contact Phone'))),
                dict(name='contact.tel_e164', display=six.text_type(_('Contact Phone - E164'))),
                dict(name='contact.uuid', display=six.text_type(_("Contact UUID"))),
                dict(name='new_contact', display=six.text_type(_('New Contact')))
            ]

            contact_variables += [dict(name="contact.%s" % scheme, display=six.text_type(_("Contact %s" % label)))
                                  for scheme, label in ContactURN.SCHEME_CHOICES if scheme != TEL_SCHEME and scheme in
                                  org.get_schemes(Channel.ROLE_SEND)]

            contact_variables += [dict(name="contact.%s" % field.key, display=field.label) for field in
                                  ContactField.objects.filter(org=org, is_active=True)]

            date_variables = [
                dict(name='date', display=six.text_type(_('Current Date and Time'))),
                dict(name='date.now', display=six.text_type(_('Current Date and Time'))),
                dict(name='date.today', display=six.text_type(_('Current Date'))),
                dict(name='date.tomorrow', display=six.text_type(_("Tomorrow's Date"))),
                dict(name='date.yesterday', display=six.text_type(_("Yesterday's Date")))
            ]

            flow_variables = [
                dict(name='channel', display=six.text_type(_('Sent to'))),
                dict(name='channel.name', display=six.text_type(_('Sent to'))),
                dict(name='channel.tel', display=six.text_type(_('Sent to'))),
                dict(name='channel.tel_e164', display=six.text_type(_('Sent to'))),
                dict(name='step', display=six.text_type(_('Sent to'))),
                dict(name='step.value', display=six.text_type(_('Sent to')))
            ]

            parent_variables = [dict(name='parent.%s' % v['name'], display=v['display']) for v in contact_variables]
            parent_variables += [dict(name='parent.%s' % v['name'], display=v['display']) for v in flow_variables]

            child_variables = [dict(name='child.%s' % v['name'], display=v['display']) for v in contact_variables]
            child_variables += [dict(name='child.%s' % v['name'], display=v['display']) for v in flow_variables]

            flow_variables.append(dict(name='flow', display=six.text_type(_('All flow variables'))))

            flow_id = self.request.GET.get('flow', None)

            if flow_id:
                # TODO: restrict this to only the possible paths to the passed in actionset uuid
                rule_sets = RuleSet.objects.filter(flow__pk=flow_id, flow__org=org)
                for rule_set in rule_sets:
                    key = ContactField.make_key(slugify(rule_set.label))
                    flow_variables.append(dict(name='flow.%s' % key, display=rule_set.label))
                    flow_variables.append(dict(name='flow.%s.category' % key, display='%s Category' % rule_set.label))
                    flow_variables.append(dict(name='flow.%s.text' % key, display='%s Text' % rule_set.label))
                    flow_variables.append(dict(name='flow.%s.time' % key, display='%s Time' % rule_set.label))

            function_completions = get_function_listing()
            messages_completions = contact_variables + date_variables + flow_variables
            messages_completions += parent_variables + child_variables
            return JsonResponse(dict(message_completions=messages_completions,
                                     function_completions=function_completions))

    class Editor(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = 'uuid'

        def derive_title(self):
            return self.object.name

        def get_template_names(self):
            return "flows/flow_editor.haml"

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Editor, self).get_context_data(*args, **kwargs)

            flow = self.get_object(self.get_queryset())
            org = self.request.user.get_org()

            # hangup any test calls if we have them
            if flow.flow_type == Flow.VOICE:
                IVRCall.hangup_test_call(flow)

            flow.ensure_current_version()

            if org:
                languages = org.languages.all().order_by('orgs')
                for lang in languages:
                    if self.get_object().base_language == lang.iso_code:
                        context['base_language'] = lang

                context['languages'] = languages

            context['has_ussd_channel'] = bool(org and org.get_ussd_channel())
            context['media_url'] = '%s://%s/' % ('http' if settings.DEBUG else 'https', settings.AWS_BUCKET_DOMAIN)
            context['is_starting'] = flow.is_starting()
            context['mutable'] = self.has_org_perm('flows.flow_update') and not self.request.user.is_superuser
            context['has_airtime_service'] = bool(flow.org.is_connected_to_transferto())
            context['can_start'] = flow.flow_type != Flow.VOICE or flow.org.supports_ivr()
            return context

        def get_gear_links(self):
            links = []
            flow = self.get_object()

            if flow.flow_type not in [Flow.SURVEY, Flow.USSD] and self.has_org_perm('flows.flow_broadcast') and not flow.is_archived:
                links.append(dict(title=_("Start Flow"), style='btn-primary', js_class='broadcast-rulesflow', href='#'))

            if self.has_org_perm('flows.flow_results'):
                links.append(dict(title=_("Results"), style='btn-primary',
                                  href=reverse('flows.flow_results', args=[flow.uuid])))
            if len(links) > 1:
                links.append(dict(divider=True)),

            if self.has_org_perm('flows.flow_update'):
                links.append(dict(title=_("Edit"), js_class='update-rulesflow', href='#'))

            if self.has_org_perm('flows.flow_copy'):
                links.append(dict(title=_("Copy"), posterize=True, href=reverse('flows.flow_copy', args=[flow.id])))

            if self.has_org_perm('orgs.org_export'):
                links.append(dict(title=_("Export"), href='%s?flow=%s' % (reverse('orgs.org_export'), flow.id)))

            if self.has_org_perm('flows.flow_revisions'):
                links.append(dict(divider=True)),
                links.append(dict(title=_("Revision History"), ngClick='showRevisionHistory()', href='#'))

            if self.has_org_perm('flows.flow_delete'):
                links.append(dict(title=_('Delete'), js_class='delete-flow', href="#"))

            return links

    class ExportResults(ModalMixin, OrgPermsMixin, SmartFormView):
        class ExportForm(forms.Form):
            flows = forms.ModelMultipleChoiceField(Flow.objects.filter(id__lt=0), required=True,
                                                   widget=forms.MultipleHiddenInput())
            contact_fields = forms.ModelMultipleChoiceField(ContactField.objects.filter(id__lt=0), required=False,
                                                            help_text=_("Which contact fields, if any, to include "
                                                                        "in the export"))

            extra_urns = forms.MultipleChoiceField(required=False, label=_("Extra URNs"),
                                                   choices=ContactURN.EXPORT_SCHEME_HEADERS,
                                                   help_text=_("Extra URNs to include in the export in addition to "
                                                               "the URN used in the flow"))

            responded_only = forms.BooleanField(required=False, label=_("Responded Only"), initial=True,
                                                help_text=_("Only export results for contacts which responded"))
            include_messages = forms.BooleanField(required=False, label=_("Include Messages"),
                                                  help_text=_("Export all messages sent and received in this flow"))
            include_runs = forms.BooleanField(required=False, label=_("Include Runs"),
                                              help_text=_("Include all runs for each contact. Leave unchecked for "
                                                          "only their most recent runs"))

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.ExportResults.ExportForm, self).__init__(*args, **kwargs)
                self.user = user
                self.fields['contact_fields'].queryset = ContactField.objects.filter(org=self.user.get_org(),
                                                                                     is_active=True)
                self.fields['flows'].queryset = Flow.objects.filter(org=self.user.get_org(), is_active=True)

            def clean(self):
                cleaned_data = super(FlowCRUDL.ExportResults.ExportForm, self).clean()

                if 'contact_fields' in cleaned_data and len(cleaned_data['contact_fields']) > 10:  # pragma: needs cover
                    raise forms.ValidationError(_("You can only include up to 10 contact fields in your export"))

                return cleaned_data

        form_class = ExportForm
        submit_button_name = _("Export")
        success_url = '@flows.flow_list'

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.ExportResults, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def derive_initial(self):
            flow_ids = self.request.GET.get('ids', None)
            if flow_ids:  # pragma: needs cover
                return dict(flows=Flow.objects.filter(org=self.request.user.get_org(), is_active=True,
                                                      id__in=flow_ids.split(',')))
            else:
                return dict()

        def form_valid(self, form):
            analytics.track(self.request.user.username, 'temba.flow_exported')

            user = self.request.user
            org = user.get_org()

            # is there already an export taking place?
            existing = ExportFlowResultsTask.get_recent_unfinished(org)
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))
            else:
                export = ExportFlowResultsTask.create(org, user, form.cleaned_data['flows'],
                                                      contact_fields=form.cleaned_data['contact_fields'],
                                                      include_runs=form.cleaned_data['include_runs'],
                                                      include_msgs=form.cleaned_data['include_messages'],
                                                      responded_only=form.cleaned_data['responded_only'],
                                                      extra_urns=form.cleaned_data['extra_urns'])
                on_transaction_commit(lambda: export_flow_results_task.delay(export.pk))

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):  # pragma: needs cover
                    messages.info(self.request,
                                  _("We are preparing your export. We will e-mail you at %s when it is ready.")
                                  % self.request.user.username)

                else:
                    export = ExportFlowResultsTask.objects.get(id=export.pk)
                    dl_url = reverse('assets.download', kwargs=dict(type='results_export', pk=export.pk))
                    messages.info(self.request,
                                  _("Export complete, you can find it here: %s (production users will get an email)")
                                  % dl_url)

            if 'HTTP_X_PJAX' not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                response = self.render_to_response(
                    self.get_context_data(form=form,
                                          success_url=self.get_success_url(),
                                          success_script=getattr(self, 'success_script', None)))
                response['Temba-Success'] = self.get_success_url()
                response['REDIRECT'] = self.get_success_url()
                return response

    class ActivityChart(OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper that renders a chart of activity by a given period
        """

        # the min number of responses to show a histogram
        HISTOGRAM_MIN = 0

        # the min number of responses to show the period charts
        PERIOD_MIN = 0

        EXIT_TYPES = {
            None: 'active',
            FlowRun.EXIT_TYPE_COMPLETED: 'completed',
            FlowRun.EXIT_TYPE_INTERRUPTED: 'interrupted',
            FlowRun.EXIT_TYPE_EXPIRED: 'expired'
        }

        def get_context_data(self, *args, **kwargs):

            total_responses = 0
            context = super(FlowCRUDL.ActivityChart, self).get_context_data(*args, **kwargs)

            flow = self.get_object()
            from temba.flows.models import FlowPathCount
            rulesets = list(flow.rule_sets.all())

            from_uuids = []
            for ruleset in rulesets:
                from_uuids += [rule.uuid for rule in ruleset.get_rules()]

            dates = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).aggregate(Max('period'), Min('period'))
            start_date = dates.get('period__min')
            end_date = dates.get('period__max')

            # by hour of the day
            hod = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra({"hour": "extract(hour from period::timestamp)"})
            hod = hod.values('hour').annotate(count=Sum('count')).order_by('hour')
            hod_dict = {int(h.get('hour')): h.get('count') for h in hod}

            hours = []
            for x in range(0, 24):
                hours.append({'bucket': datetime(1970, 1, 1, hour=x), 'count': hod_dict.get(x, 0)})

            # by day of the week
            dow = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra({"day": "extract(dow from period::timestamp)"})
            dow = dow.values('day').annotate(count=Sum('count'))
            dow_dict = {int(d.get('day')): d.get('count') for d in dow}

            dow = []
            for x in range(0, 7):
                day_count = dow_dict.get(x, 0)
                dow.append({'day': x, 'count': day_count})
                total_responses += day_count

            if total_responses > self.PERIOD_MIN:
                dow = sorted(dow, key=lambda k: k['day'])
                days = (_('Sunday'), _('Monday'), _('Tuesday'), _('Wednesday'), _('Thursday'), _('Friday'), _('Saturday'))
                dow = [{'day': days[d['day']], 'count': d['count'],
                        'pct': 100 * float(d['count']) / float(total_responses)} for d in dow]
                context['dow'] = dow
                context['hod'] = hours

            if total_responses > self.HISTOGRAM_MIN:
                # our main histogram
                date_range = end_date - start_date
                histogram = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids)
                if date_range < timedelta(days=21):
                    histogram = histogram.extra({"bucket": "date_trunc('hour', period)"})
                    min_date = start_date - timedelta(hours=1)
                elif date_range < timedelta(days=500):
                    histogram = histogram.extra({"bucket": "date_trunc('day', period)"})
                    min_date = end_date - timedelta(days=100)
                else:
                    histogram = histogram.extra({"bucket": "date_trunc('week', period)"})
                    min_date = end_date - timedelta(days=500)

                histogram = histogram.values('bucket').annotate(count=Sum('count')).order_by('bucket')
                context['histogram'] = histogram

                # highcharts works in UTC, but we want to offset our chart according to the org timezone
                context['min_date'] = min_date

            counts = FlowRunCount.objects.filter(flow=flow).values('exit_type').annotate(Sum('count'))

            total_runs = 0
            for count in counts:
                key = self.EXIT_TYPES[count['exit_type']]
                context[key] = count['count__sum']
                total_runs += count['count__sum']

            # make sure we have a value for each one
            for state in ('expired', 'interrupted', 'completed', 'active'):
                if state not in context:
                    context[state] = 0

            context['total_runs'] = total_runs
            context['total_responses'] = total_responses

            return context

    class RunTable(OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper which renders rows of runs to be embedded in an existing table with infinite scrolling
        """

        paginate_by = 50

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.RunTable, self).get_context_data(*args, **kwargs)
            flow = self.get_object()
            org = self.derive_org()

            context['rulesets'] = list(flow.rule_sets.filter(ruleset_type__in=RuleSet.TYPE_WAIT).order_by('y'))
            for ruleset in context['rulesets']:
                rules = len(ruleset.get_rules())
                ruleset.category = 'true' if rules > 1 else 'false'

            test_contacts = Contact.objects.filter(org=org, is_test=True).values_list('id', flat=True)

            runs = FlowRun.objects.filter(flow=flow, responded=True).exclude(contact__in=test_contacts)
            query = self.request.GET.get('q', None)
            contact_ids = []
            if query:
                query = query.strip()
                contact_ids = list(Contact.objects.filter(org=flow.org, name__icontains=query).exclude(id__in=test_contacts).values_list('id', flat=True))
                query = query.replace("-", "")
                contact_ids += list(ContactURN.objects.filter(org=flow.org, path__icontains=query).exclude(contact__in=test_contacts).order_by('contact__id').distinct('contact__id').values_list('contact__id', flat=True))
                runs = runs.filter(contact__in=contact_ids)

            # paginate
            modified_on = self.request.GET.get('modified_on', None)
            if modified_on:
                id = self.request.GET['id']

                from temba.utils import json_date_to_datetime
                modified_on = json_date_to_datetime(modified_on)
                runs = runs.filter(modified_on__lte=modified_on).exclude(id__gte=id)

            # we grab one more than our page to denote whether there's more to get
            runs = list(runs.order_by('-modified_on')[:self.paginate_by + 1])
            context['more'] = len(runs) > self.paginate_by
            runs = runs[:self.paginate_by]

            # populate ruleset values
            for run in runs:
                results = run.results
                run.value_list = []
                for ruleset in context['rulesets']:
                    key = Flow.label_to_slug(ruleset.label)
                    run.value_list.append(results.get(key, None))

            context['runs'] = runs
            context['paginate_by'] = self.paginate_by
            return context

    class CategoryCounts(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = 'uuid'

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse(self.get_object().get_category_counts())

    class Results(OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = 'uuid'

        def get_gear_links(self):
            links = []

            if self.has_org_perm('flows.flow_update'):
                links.append(dict(title=_('Download'),
                                  href='#',
                                  js_class="download-results"))

            if self.has_org_perm('flows.flow_editor'):
                links.append(dict(title=_("Edit Flow"),
                                  style='btn-primary',
                                  href=reverse('flows.flow_editor', args=[self.get_object().uuid])))

            return links

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Results, self).get_context_data(*args, **kwargs)
            flow = self.get_object()
            context['rulesets'] = list(flow.rule_sets.filter(ruleset_type__in=RuleSet.TYPE_WAIT).order_by('y'))
            for ruleset in context['rulesets']:
                rules = len(ruleset.get_rules())
                ruleset.category = 'true' if rules > 1 else 'false'
            context['categories'] = flow.get_category_counts()['counts']
            context['utcoffset'] = int(datetime.now(flow.org.timezone).utcoffset().total_seconds() // 60)
            return context

    class Activity(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            flow = self.get_object(self.get_queryset())
            (active, visited) = flow.get_activity()

            return JsonResponse(dict(activity=active, visited=visited, is_starting=flow.is_starting()))

    class Simulate(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            return HttpResponseRedirect(reverse('flows.flow_editor', args=[self.get_object().uuid]))

        def post(self, request, *args, **kwargs):

            # try to parse our body
            try:
                json_dict = json.loads(request.body)
            except Exception as e:  # pragma: needs cover
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            if json_dict.get("version", None) == "1":
                return self.handle_legacy(request, json_dict)
            else:

                # handle via the new engine
                client = get_client()

                # simulating never caches
                asset_timestamp = int(time.time() * 1000000)
                flow = self.get_object(self.get_queryset())

                # we control the pointers to ourselves and environment ignoring what the client might send
                flow_request = client.request_builder(asset_timestamp).asset_server(flow.org)

                # when testing, we need to include all of our assets
                if settings.TESTING:
                    flow_request.include_all(flow.org)

                flow_request.request['events'] = json_dict.get('events')

                # check if we are triggering a new session
                if 'trigger' in json_dict:
                    flow_request.request['trigger'] = json_dict.get('trigger')
                    output = client.start(flow_request.request)
                    return JsonResponse(output.as_json())

                # otherwise we are resuming
                else:
                    session = json_dict.get('session')
                    flow_request.request['events'] = json_dict.get('events')
                    output = flow_request.resume(session)
                    return JsonResponse(output.as_json())

        def handle_legacy(self, request, json_dict):

            Contact.set_simulation(True)
            user = self.request.user
            test_contact = Contact.get_test_contact(user)
            flow = self.get_object(self.get_queryset())

            if json_dict and json_dict.get('hangup', False):  # pragma: needs cover
                # hangup any test calls if we have them
                IVRCall.hangup_test_call(self.get_object())
                return JsonResponse(dict(status="success", message="Test call hung up"))

            if json_dict and json_dict.get('has_refresh', False):

                lang = request.GET.get('lang', None)
                if lang:
                    test_contact.language = lang
                    test_contact.save()

                # delete all our steps and messages to restart the simulation
                runs = FlowRun.objects.filter(contact=test_contact).order_by('-modified_on')
                steps = FlowStep.objects.filter(run__in=runs)

                # if their last simulation was more than a day ago, log this simulation
                if runs and runs.first().created_on < timezone.now() - timedelta(hours=24):  # pragma: needs cover
                    analytics.track(user.username, 'temba.flow_simulated')

                action_log_ids = list(ActionLog.objects.filter(run__in=runs).values_list('id', flat=True))
                ActionLog.objects.filter(id__in=action_log_ids).delete()

                msg_ids = list(Msg.objects.filter(contact=test_contact).only('id').values_list('id', flat=True))

                for batch in chunk_list(msg_ids, 25):
                    Msg.objects.filter(id__in=list(batch)).delete()

                IVRCall.objects.filter(contact=test_contact).delete()
                USSDSession.objects.filter(contact=test_contact).delete()

                steps.delete()
                FlowRun.objects.filter(contact=test_contact).delete()

                # reset all contact fields values
                test_contact.values.all().delete()

                # reset the name for our test contact too
                test_contact.name = "%s %s" % (request.user.first_name, request.user.last_name)
                test_contact.save()

                # reset the groups for test contact
                for group in test_contact.all_groups.all():
                    group.update_contacts(request.user, [test_contact], False)

                flow.start([], [test_contact], restart_participants=True)

            # try to create message
            new_message = json_dict.get('new_message', '')
            media = None

            media_url = 'http://%s%simages' % (user.get_org().get_brand_domain(), settings.STATIC_URL)

            if 'new_photo' in json_dict:  # pragma: needs cover
                media = '%s/png:%s/simulator_photo.png' % (Msg.MEDIA_IMAGE, media_url)
            elif 'new_gps' in json_dict:  # pragma: needs cover
                media = '%s:47.6089533,-122.34177' % Msg.MEDIA_GPS
            elif 'new_video' in json_dict:  # pragma: needs cover
                media = '%s/mp4:%s/simulator_video.mp4' % (Msg.MEDIA_VIDEO, media_url)
            elif 'new_audio' in json_dict:  # pragma: needs cover
                media = '%s/mp4:%s/simulator_audio.m4a' % (Msg.MEDIA_AUDIO, media_url)

            if new_message or media:
                try:
                    if flow.flow_type == Flow.USSD:
                        if new_message == "__interrupt__":
                            status = USSDSession.INTERRUPTED
                        else:
                            status = None
                        USSDSession.handle_incoming(test_contact.org.get_ussd_channel(contact_urn=test_contact.get_urn(TEL_SCHEME)),
                                                    test_contact.get_urn(TEL_SCHEME).path,
                                                    content=new_message,
                                                    contact=test_contact,
                                                    date=timezone.now(),
                                                    message_id=str(randint(0, 1000)),
                                                    external_id='test',
                                                    org=user.get_org(),
                                                    status=status)
                    else:
                        Msg.create_incoming(None,
                                            six.text_type(test_contact.get_urn(TEL_SCHEME)),
                                            new_message,
                                            attachments=[media] if media else None,
                                            org=user.get_org(),
                                            status=PENDING)
                except Exception as e:  # pragma: needs cover

                    traceback.print_exc()
                    return JsonResponse(dict(status="error", description="Error creating message: %s" % str(e)),
                                        status=400)

            messages = Msg.objects.filter(contact=test_contact).order_by('pk', 'created_on')

            if flow.flow_type == Flow.USSD:
                for msg in messages:
                    if msg.connection.should_end:
                        msg.connection.close()

                # don't show the empty closing message on the simulator
                messages = messages.exclude(text='', direction='O')

            action_logs = ActionLog.objects.filter(run__contact=test_contact).order_by('pk', 'created_on')

            messages_and_logs = chain(messages, action_logs)
            messages_and_logs = sorted(messages_and_logs, key=cmp_to_key(msg_log_cmp))

            messages_json = []
            if messages_and_logs:
                for msg in messages_and_logs:
                    messages_json.append(msg.simulator_json())

            (active, visited) = flow.get_activity(simulation=True)
            response = dict(messages=messages_json, activity=active, visited=visited)

            # if we are at a ruleset, include it's details
            step = FlowStep.objects.filter(contact=test_contact, left_on=None).order_by('-arrived_on').first()
            if step:
                ruleset = RuleSet.objects.filter(uuid=step.step_uuid).first()
                if ruleset:
                    response['ruleset'] = ruleset.as_json()

            return JsonResponse(dict(status="success", description="Message sent to Flow", **response))

    class Json(OrgObjPermsMixin, SmartUpdateView):
        success_message = ''

        def get(self, request, *args, **kwargs):

            flow = self.get_object()
            flow.ensure_current_version()

            # all the translation languages for our org
            languages = [lang.as_json() for lang in flow.org.languages.all().order_by('orgs')]

            # all countries we have a channel for, never fail here
            try:
                channel_countries = flow.org.get_channel_countries()
            except Exception:  # pragma: needs cover
                logger.error('Unable to get currency for channel countries.', exc_info=True)
                channel_countries = []

            # all the channels available for our org
            channels = [dict(uuid=chan.uuid, name=u"%s: %s" % (chan.get_channel_type_display(), chan.get_address_display())) for chan in flow.org.channels.filter(is_active=True)]
            return JsonResponse(dict(flow=flow.as_json(expand_contacts=True), languages=languages,
                                     channel_countries=channel_countries, channels=channels))

        def post(self, request, *args, **kwargs):

            # require update permissions
            if not self.has_org_perm('flows.flow_update'):
                return HttpResponseRedirect(reverse('flows.flow_json', args=[self.get_object().pk]))

            # try to parse our body
            json_string = force_text(request.body)

            # if the last modified on this flow is more than a day ago, log that this flow as updated
            if self.get_object().saved_on < timezone.now() - timedelta(hours=24):  # pragma: needs cover
                analytics.track(self.request.user.username, 'temba.flow_updated')

            # try to save the our flow, if this fails, let's let that bubble up to our logger
            json_dict = json.loads(json_string)
            print(json.dumps(json_dict, indent=2))

            try:
                flow = self.get_object(self.get_queryset())
                revision = flow.update(json_dict, user=self.request.user)
                return JsonResponse({
                    'status': "success",
                    'saved_on': datetime_to_str(flow.saved_on),
                    'revision': revision.revision
                }, status=200)

            except FlowInvalidCycleException:
                error = _("Your flow contains an invalid loop. Please refresh your browser.")
            except FlowVersionConflictException:
                error = _("Your flow has been upgraded to the latest version. "
                          "In order to continue editing, please refresh your browser.")
            except FlowUserConflictException as e:
                error = _("%s is currently editing this Flow. "
                          "Your changes will not be saved until you refresh your browser.") % e.other_user
            except Exception:  # pragma: no cover
                error = _("Your flow could not be saved. Please refresh your browser.")

            return JsonResponse({'status': "failure", 'description': error}, status=400)

    class Broadcast(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class BroadcastForm(forms.ModelForm):
            def __init__(self, *args, **kwargs):
                self.user = kwargs.pop('user')
                self.flow = kwargs.pop('flow')

                super(FlowCRUDL.Broadcast.BroadcastForm, self).__init__(*args, **kwargs)
                self.fields['omnibox'].set_user(self.user)

            omnibox = OmniboxField(label=_("Contacts & Groups"),
                                   help_text=_("These contacts will be added to the flow, sending the first message if appropriate."))

            restart_participants = forms.BooleanField(label=_("Restart Participants"), required=False, initial=False,
                                                      help_text=_("Restart any contacts already participating in this flow"))

            include_active = forms.BooleanField(label=_("Include Active Contacts"), required=False, initial=False,
                                                help_text=_("Include contacts currently active in a flow"))

            def clean_omnibox(self):
                starting = self.cleaned_data['omnibox']
                if not starting['groups'] and not starting['contacts']:  # pragma: needs cover
                    raise ValidationError(_("You must specify at least one contact or one group to start a flow."))

                return starting

            def clean(self):
                cleaned = super(FlowCRUDL.Broadcast.BroadcastForm, self).clean()

                # check whether there are any flow starts that are incomplete
                if self.flow.is_starting():
                    raise ValidationError(_("This flow is already being started, please wait until that process is complete before starting more contacts."))

                if self.flow.org.is_suspended():
                    raise ValidationError(_("Sorry, your account is currently suspended. To enable sending messages, please contact support."))

                return cleaned

            class Meta:
                model = Flow
                fields = ('omnibox', 'restart_participants', 'include_active')

        form_class = BroadcastForm
        fields = ('omnibox', 'restart_participants', 'include_active')
        success_message = ''
        submit_button_name = _("Add Contacts to Flow")
        success_url = 'uuid@flows.flow_editor'

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Broadcast, self).get_context_data(*args, **kwargs)

            run_stats = self.object.get_run_stats()
            context['run_count'] = run_stats['total']
            context['complete_count'] = run_stats['completed']
            return context

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Broadcast, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            kwargs['flow'] = self.object
            return kwargs

        def save(self, *args, **kwargs):
            form = self.form
            flow = self.object

            # save off our broadcast info
            omnibox = form.cleaned_data['omnibox']

            analytics.track(self.request.user.username, 'temba.flow_broadcast',
                            dict(contacts=len(omnibox['contacts']), groups=len(omnibox['groups'])))

            # activate all our contacts
            flow.async_start(self.request.user,
                             list(omnibox['groups']), list(omnibox['contacts']),
                             restart_participants=form.cleaned_data['restart_participants'],
                             include_active=form.cleaned_data['include_active'])
            return flow

    class Assets(OrgPermsMixin, SmartTemplateView):
        """
        Flow assets endpoint used by goflow engine and standalone flow editor. For example:

        /flow_assets/123/xyz/flow/0a9f4ddd-895d-4c64-917e-b004fb048306     -> the flow with that UUID in org #123
        /flow_assets/123/xyz/channel/b432261a-7117-4885-8815-8f04e7a15779  -> the channel with that UUID in org #123
        /flow_assets/123/xyz/group                                         -> all groups for org #123
        /flow_assets/123/xyz/location_hierarchy                            -> country>states>districts>wards for org #123
        """
        class Resource(object):
            def __init__(self, queryset, serializer):
                self.queryset = queryset
                self.serializer = serializer

            def get_root(self, org):
                return self.queryset.filter(org=org).order_by('id')

            def get_item(self, org, uuid):
                return self.get_root(org).filter(uuid=uuid).first()

        class BoundaryResource(object):
            def __init__(self, serializer):
                self.serializer = serializer

            def get_root(self, org):
                return org.country

        resources = {
            'channel': Resource(Channel.objects.filter(is_active=True), goflow.serialize_channel),
            'field': Resource(ContactField.objects.filter(is_active=True), goflow.serialize_field),
            'flow': Resource(Flow.objects.filter(is_active=True, is_archived=False), goflow.serialize_flow),
            'group': Resource(ContactGroup.user_groups.filter(is_active=True, status=ContactGroup.STATUS_READY), goflow.serialize_group),
            'label': Resource(Label.label_objects.filter(is_active=True), goflow.serialize_label),
            'location_hierarchy': BoundaryResource(goflow.serialize_location_hierarchy),
        }

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r'^%s/%s/(?P<org>\d+)/(?P<fingerprint>[\w-]+)/(?P<type>\w+)/((?P<uuid>[a-z0-9-]{36})/)?$' % (path, action)

        def derive_org(self):
            if not hasattr(self, 'org'):
                self.org = Org.objects.get(id=self.kwargs['org'])
            return self.org

        def has_permission(self, request, *args, **kwargs):
            # allow requests from the flowserver using token authentication
            if request.user.is_anonymous() and settings.FLOW_SERVER_AUTH_TOKEN:
                authorization = request.META.get('HTTP_AUTHORIZATION', '').split(' ')
                if len(authorization) == 2 and authorization[0] == 'Token' and authorization[1] == settings.FLOW_SERVER_AUTH_TOKEN:
                    return True

            return super(FlowCRUDL.Assets, self).has_permission(request, *args, **kwargs)

        def get(self, *args, **kwargs):
            org = self.derive_org()
            uuid = kwargs.get('uuid')

            resource = self.resources[kwargs['type']]
            if uuid:
                result = resource.get_item(org, uuid)
            else:
                result = resource.get_root(org)

            if isinstance(result, QuerySet):
                page_size = self.request.GET.get('page_size')
                page_num = self.request.GET.get('page')

                if page_size is None:
                    # the flow engine doesn't want results paged, so just return the entire set
                    return JsonResponse([resource.serializer(o) for o in result], safe=False)
                else:  # pragma: no cover
                    # TODO make this meet the needs of the new editor
                    paginator = Paginator(result, page_size)
                    page = paginator.page(page_num)

                    return JsonResponse({
                        'results': [resource.serializer(o) for o in page.object_list],
                        'has_next': page.has_next()
                    })
            else:
                return JsonResponse(resource.serializer(result))


# this is just for adhoc testing of the preprocess url
class PreprocessTest(FormView):  # pragma: no cover

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(PreprocessTest, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse(json.dumps(dict(text='Norbert', extra=dict(occupation='hoopster', skillz=7.9))),
                            content_type='application/json')


class FlowLabelForm(forms.ModelForm):
    name = forms.CharField(required=True)
    parent = forms.ModelChoiceField(FlowLabel.objects.all(), required=False, label=_("Parent"))
    flows = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, *args, **kwargs):
        self.org = kwargs['org']
        del kwargs['org']

        label = None
        if 'label' in kwargs:
            label = kwargs['label']
            del kwargs['label']

        super(FlowLabelForm, self).__init__(*args, **kwargs)
        qs = FlowLabel.objects.filter(org=self.org, parent=None)

        if label:
            qs = qs.exclude(id=label.pk)

        self.fields['parent'].queryset = qs

    def clean_name(self):
        name = self.cleaned_data['name'].strip()
        if FlowLabel.objects.filter(org=self.org, name=name).exclude(pk=self.instance.id).exists():
            raise ValidationError(_("Name already used"))
        return name

    class Meta:
        model = FlowLabel
        fields = '__all__'


class FlowLabelCRUDL(SmartCRUDL):
    model = FlowLabel
    actions = ('create', 'update', 'delete')

    class Delete(OrgObjPermsMixin, SmartDeleteView):
        redirect_url = "@flows.flow_list"
        cancel_url = "@flows.flow_list"
        success_message = ''

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = FlowLabelForm
        success_url = 'id@flows.flow_filter'
        success_message = ''

        def get_form_kwargs(self):
            kwargs = super(FlowLabelCRUDL.Update, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            kwargs['label'] = self.get_object()
            return kwargs

        def derive_fields(self):
            return ('name', 'parent')

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ('name', 'parent', 'flows')
        success_url = '@flows.flow_list'
        form_class = FlowLabelForm
        success_message = ''
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super(FlowLabelCRUDL.Create, self).get_form_kwargs()
            kwargs['org'] = self.request.user.get_org()
            return kwargs

        def pre_save(self, obj, *args, **kwargs):
            obj = super(FlowLabelCRUDL.Create, self).pre_save(obj, *args, **kwargs)
            obj.org = self.request.user.get_org()
            return obj

        def post_save(self, obj, *args, **kwargs):
            obj = super(FlowLabelCRUDL.Create, self).post_save(obj, *args, **kwargs)

            flow_ids = []
            if self.form.cleaned_data['flows']:  # pragma: needs cover
                flow_ids = [int(f) for f in self.form.cleaned_data['flows'].split(',') if f.isdigit()]

            flows = Flow.objects.filter(org=obj.org, is_active=True, pk__in=flow_ids)

            if flows:  # pragma: needs cover
                obj.toggle_label(flows, add=True)

            return obj
