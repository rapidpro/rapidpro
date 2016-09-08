from __future__ import unicode_literals

import json
import logging
import regex
import traceback

from collections import Counter
from datetime import datetime, timedelta
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.files.storage import default_storage
from django.core.urlresolvers import reverse
from django.db.models import Count, Q, Max
from django import forms
from django.http import HttpResponse, HttpResponseRedirect
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView
from itertools import chain
from smartmin.views import SmartCRUDL, SmartCreateView, SmartReadView, SmartListView, SmartUpdateView
from smartmin.views import SmartDeleteView, SmartTemplateView, SmartFormView
from temba.contacts.fields import OmniboxField
from temba.contacts.models import Contact, ContactGroup, ContactField, TEL_SCHEME
from temba.formax import FormaxMixin
from temba.ivr.models import IVRCall
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.reports.models import Report
from temba.flows.models import Flow, FlowRun, FlowRevision
from temba.flows.tasks import export_flow_results_task
from temba.locations.models import AdminBoundary
from temba.msgs.models import Msg, INCOMING, OUTGOING, PENDING, INTERRUPTED
from temba.triggers.models import Trigger
from temba.utils import analytics, build_json_response, percentage, datetime_to_str
from temba.utils.expressions import get_function_listing
from temba.utils.views import BaseActionForm
from temba.values.models import Value
from .models import FlowStep, RuleSet, ActionLog, ExportFlowResultsTask, FlowLabel, FlowStart

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


class BaseFlowForm(forms.ModelForm):
    expires_after_minutes = forms.ChoiceField(label=_('Expire inactive contacts'),
                                              help_text=_("When inactive contacts should be removed from the flow"),
                                              initial=str(60 * 24 * 7),
                                              choices=EXPIRES_CHOICES)

    def clean_keyword_triggers(self):
        org = self.user.get_org()
        wrong_format = []
        existing_keywords = []
        keyword_triggers = self.cleaned_data.get('keyword_triggers', '').strip()

        for keyword in keyword_triggers.split(','):
            if keyword and not regex.match('^\w+$', keyword, flags=regex.UNICODE | regex.V0):
                wrong_format.append(keyword)

            # make sure it is unique on this org
            if keyword and org:
                existing = Trigger.objects.filter(org=org, keyword__iexact=keyword, is_archived=False, is_active=True)

                if self.instance:
                    existing = existing.exclude(flow=self.instance.pk)

                if existing:
                    existing_keywords.append(keyword)

        if wrong_format:
            raise forms.ValidationError(_('"%s" must be a single word containing only letter and numbers') % ', '.join(wrong_format))

        if existing_keywords:
            if len(existing_keywords) > 1:
                error_message = _('The keywords "%s" are already used for another flow') % ', '.join(existing_keywords)
            else:
                error_message = _('The keyword "%s" is already used for another flow') % ', '.join(existing_keywords)
            raise forms.ValidationError(error_message)

        return keyword_triggers.lower()

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

        if form.is_valid():
            form.execute()

        return self.get(request, *args, **kwargs)


class RuleCRUDL(SmartCRUDL):
    actions = ('results', 'analytics', 'map', 'choropleth')
    model = RuleSet

    class Map(OrgPermsMixin, SmartReadView):
        pass

    class Results(OrgPermsMixin, SmartReadView):

        def get_context_data(self, **kwargs):
            filters = json.loads(self.request.GET.get('filters', '[]'))
            segment = json.loads(self.request.GET.get('segment', 'null'))

            ruleset = self.get_object()
            results = Value.get_value_summary(ruleset=ruleset, filters=filters, segment=segment)
            return dict(id=ruleset.pk, label=ruleset.label, results=results)

        def render_to_response(self, context, **response_kwargs):
            response = HttpResponse(json.dumps(context), content_type='application/javascript')
            return response

    class Choropleth(OrgPermsMixin, SmartReadView):

        def get_context_data(self, **kwargs):
            filters = json.loads(self.request.GET.get('filters', '[]'))

            ruleset = self.get_object()
            flow = ruleset.flow
            org = flow.org

            country = self.derive_org().country
            parent_osm_id = self.request.GET.get('boundary', country.osm_id)
            parent = AdminBoundary.objects.get(osm_id=parent_osm_id)

            # figure out our state and district contact fields
            state_field = ContactField.objects.filter(org=org, value_type=Value.TYPE_STATE).first()
            district_field = ContactField.objects.filter(org=org, value_type=Value.TYPE_DISTRICT).first()
            ward_field = ContactField.objects.filter(org=org, value_type=Value.TYPE_WARD).first()
            # by default, segment by states
            segment = dict(location=state_field.label)
            if parent.level == 1:
                segment = dict(location=district_field.label, parent=parent.osm_id)
            if parent.level == 2:
                segment = dict(location=ward_field.label, parent=parent.osm_id)

            results = Value.get_value_summary(ruleset=ruleset, filters=filters, segment=segment)

            # build our totals
            category_counts = Counter()
            for result in results:
                for category in result['categories']:
                    category_counts[category['label']] += category['count']

            # find our primary category
            prime_category = None
            for category, count in category_counts.items():
                if not prime_category or count > prime_category['count']:
                    prime_category = dict(label=category, count=count)

            # build our secondary category, possibly grouping all secondary categories in Others
            other_category = None
            for category, count in category_counts.items():
                if category != prime_category['label']:
                    if not other_category:
                        other_category = dict(label=category, count=count)
                    else:
                        other_category['label'] = "Others"
                        other_category['count'] += count

            if prime_category is None:
                prime_category = dict(label="", count=0)

            if other_category is None:
                other_category = dict(label="", count=0)

            total = prime_category['count'] + other_category['count']
            prime_category['percentage'] = percentage(prime_category['count'], total)
            other_category['percentage'] = percentage(other_category['count'], total)

            totals = dict(name=parent.name,
                          count=total,
                          results=[prime_category, other_category])
            categories = [prime_category['label'], other_category['label']]

            # calculate our percentages per segment
            scores = dict()
            for result in results:
                prime_count = 0
                other_count = 0
                for category in result['categories']:
                    if category['label'] == prime_category['label']:
                        prime_count = category['count']
                    else:
                        other_count += category['count']

                total = prime_count + other_count
                score = 1.0 * prime_count / total if total else 0
                results = [dict(count=prime_count,
                                percentage=percentage(prime_count, total),
                                label=prime_category['label']),
                           dict(count=other_count,
                                percentage=percentage(other_count, total),
                                label=other_category['label'])]
                scores[result['boundary']] = dict(count=total,
                                                  score=score,
                                                  results=results,
                                                  name=result['label'])

            breaks = [.2, .3, .35, .40, .45, .55, .60, .65, .7, .8, 1]
            return dict(breaks=breaks, totals=totals, scores=scores, categories=categories)

    class Analytics(OrgPermsMixin, SmartTemplateView):
        title = "Analytics"

        def get_context_data(self, **kwargs):
            def dthandler(obj):
                return obj.isoformat() if isinstance(obj, datetime) else obj

            org = self.request.user.get_org()
            rules = RuleSet.objects.filter(flow__is_active=True, flow__org=org).exclude(label=None).order_by('flow__created_on', 'y').select_related('flow')
            current_flow = None
            flow_json = []

            # group our rules by flow, calculating # of contacts participating in each flow
            for rule in rules:
                if current_flow is None or current_flow['id'] != rule.flow_id:
                    if current_flow and len(current_flow['rules']) > 0:
                        flow_json.append(current_flow)

                    flow = rule.flow
                    current_flow = dict(id=flow.id,
                                        text=flow.name,
                                        rules=[],
                                        stats=dict(runs=flow.get_total_runs(),
                                                   created_on=flow.created_on))

                current_flow['rules'].append(dict(text=rule.label, id=rule.pk, flow=current_flow['id'],
                                                  stats=dict(created_on=rule.created_on)))

            # append our last flow if appropriate
            if current_flow and len(current_flow['rules']) > 0:
                flow_json.append(current_flow)

            groups = ContactGroup.user_groups.filter(org=org).order_by('name')
            groups_json = []
            for group in groups:
                if group.get_member_count() > 0:
                    groups_json.append(group.analytics_json())

            reports = Report.objects.filter(is_active=True, org=org).order_by('title')
            reports_json = []
            for report in reports:
                reports_json.append(report.as_json())

            current_report = None
            edit_report = self.request.REQUEST.get('edit_report', None)
            if edit_report and int(edit_report):
                request_report = Report.objects.filter(pk=edit_report, org=org).first()
                if request_report:
                    current_report = json.dumps(request_report.as_json())

            state_fields = org.contactfields.filter(value_type=Value.TYPE_STATE)
            district_fields = org.contactfields.filter(value_type=Value.TYPE_DISTRICT)
            org_supports_map = org.country and state_fields.first() and district_fields.first()

            return dict(flows=json.dumps(flow_json, default=dthandler), org_supports_map=org_supports_map,
                        groups=json.dumps(groups_json), reports=json.dumps(reports_json), current_report=current_report)


def msg_log_cmp(a, b):
    if a.__class__ == b.__class__:
        return a.pk - b.pk
    else:
        if a.created_on == b.created_on:
            return 0
        elif a.created_on < b.created_on:
            return -1
        else:
            return 1


class PartialTemplate(SmartTemplateView):

    def pre_process(self, request, *args, **kwargs):
        self.template = kwargs['template']
        return

    def get_template_names(self):
        return "partials/%s.html" % self.template


class FlowCRUDL(SmartCRUDL):
    actions = ('list', 'archived', 'copy', 'create', 'delete', 'update', 'simulate', 'export_results',
               'upload_action_recording', 'read', 'editor', 'results', 'json', 'broadcast', 'activity', 'filter',
               'completion', 'revisions', 'recent_messages')

    model = Flow

    class RecentMessages(OrgObjPermsMixin, SmartReadView):
        def get(self, request, *args, **kwargs):
            step_uuid = request.REQUEST.get('step', None)
            next_uuid = request.REQUEST.get('destination', None)
            rule_uuid = request.REQUEST.get('rule', None)

            recent_messages = []

            # noop if we are missing needed parameters
            if not step_uuid or not next_uuid:
                return build_json_response(recent_messages)

            if rule_uuid:
                rule_uuids = rule_uuid.split(',')
                recent_steps = FlowStep.objects.filter(step_uuid=step_uuid,
                                                       next_uuid=next_uuid,
                                                       rule_uuid__in=rule_uuids).order_by('-left_on')[:20].prefetch_related('messages', 'contact')
                msg_direction_filter = INCOMING

            else:
                recent_steps = FlowStep.objects.filter(step_uuid=step_uuid,
                                                       next_uuid=next_uuid,
                                                       rule_uuid=None).order_by('-left_on')[:20].prefetch_related('messages', 'contact')

                msg_direction_filter = OUTGOING

            # this is slightly goofy for performance reasons, we don't want to do the big join, so instead use the
            # prefetch related above and do the filtering ourselves
            for step in recent_steps:
                if not step.contact.is_test:
                    for msg in step.messages.all():
                        if msg.visibility == Msg.VISIBILITY_VISIBLE and msg.direction == msg_direction_filter:
                            recent_messages.append(dict(sent=datetime_to_str(msg.created_on),
                                                        text=msg.text))

            return build_json_response(recent_messages[:5])

    class Revisions(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            flow = self.get_object()

            revision_id = request.REQUEST.get('definition', None)

            if revision_id:
                revision = FlowRevision.objects.get(flow=flow, pk=revision_id)
                return build_json_response(revision.get_definition_json())
            else:
                revisions = []
                for revision in flow.revisions.all().order_by('-created_on')[:25]:
                    # validate the flow defintion before presenting it to the user
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

                return build_json_response(revisions)

    class OrgQuerysetMixin(object):
        def derive_queryset(self, *args, **kwargs):
            queryset = super(FlowCRUDL.OrgQuerysetMixin, self).derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated():
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
                fields = ('name', 'keyword_triggers', 'expires_after_minutes', 'flow_type', 'base_language')

        form_class = FlowCreateForm
        success_url = 'id@flows.flow_editor'
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

            if not obj.flow_type:
                obj.flow_type = Flow.FLOW

            # if we don't have a language, use base
            if not obj.base_language:
                obj.base_language = 'base'

            self.object = Flow.create(org, self.request.user, obj.name,
                                      flow_type=obj.flow_type, expires_after_minutes=obj.expires_after_minutes,
                                      base_language=obj.base_language)

        def post_save(self, obj):
            user = self.request.user
            org = user.get_org()

            # create triggers for this flow only if there are keywords
            if len(self.form.cleaned_data['keyword_triggers']) > 0:
                for keyword in self.form.cleaned_data['keyword_triggers'].split(','):
                    Trigger.objects.create(org=org, keyword=keyword, flow=obj, created_by=user, modified_by=user)

            return obj

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ('pk',)
        cancel_url = 'id@flows.flow_editor'
        redirect_url = '@flows.flow_list'
        default_template = 'smartmin/delete_confirm.html'
        success_message = _("Your flow has been removed.")

        def post(self, request, *args, **kwargs):
            self.get_object().release()
            redirect_url = self.get_redirect_url()

            return HttpResponseRedirect(redirect_url)

    class Copy(OrgObjPermsMixin, SmartUpdateView):
        fields = []
        success_message = ''

        def form_valid(self, form):
            # copy our current object
            copy = Flow.copy(self.object, self.request.user)

            # redirect to the newly created flow
            return HttpResponseRedirect(reverse('flows.flow_editor', args=[copy.pk]))

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class FlowUpdateForm(BaseFlowForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow")
            )

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.Update.FlowUpdateForm, self).__init__(*args, **kwargs)
                self.user = user

                metadata = self.instance.get_metadata_json()
                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org, flow=self.instance, is_archived=False, groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD
                ).order_by('created_on')

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

                self.fields['keyword_triggers'].initial = ','.join([t.keyword for t in flow_triggers])

            class Meta:
                model = Flow
                fields = ('name', 'keyword_triggers', 'labels', 'base_language', 'expires_after_minutes', 'ignore_triggers')

        success_message = ''
        fields = ('name', 'keyword_triggers', 'expires_after_minutes', 'ignore_triggers')
        form_class = FlowUpdateForm

        def derive_fields(self):
            fields = [field for field in self.fields]

            obj = self.get_object()
            if not obj.base_language and self.org.primary_language:
                fields += ['base_language']

            if obj.flow_type == Flow.SURVEY:
                fields.insert(len(fields) - 1, Flow.CONTACT_CREATION)

            return fields

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def pre_save(self, obj):
            obj = super(FlowCRUDL.Update, self).pre_save(obj)
            metadata = obj.get_metadata_json()

            if Flow.CONTACT_CREATION in self.form.cleaned_data:
                metadata[Flow.CONTACT_CREATION] = self.form.cleaned_data[Flow.CONTACT_CREATION]
            obj.set_metadata_json(metadata)
            return obj

        def post_save(self, obj):
            keywords = set()
            user = self.request.user
            org = user.get_org()
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
                if keyword in archived_keywords:
                    obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None).update(is_archived=False)
                else:
                    Trigger.objects.create(org=org, keyword=keyword, trigger_type=Trigger.TYPE_KEYWORD,
                                           flow=obj, created_by=user, modified_by=user)

            # run async task to update all runs
            from .tasks import update_run_expirations_task
            update_run_expirations_task.delay(obj.pk)

            return obj

    class UploadActionRecording(OrgPermsMixin, SmartUpdateView):
        def post(self, request, *args, **kwargs):
            path = self.save_recording_upload(self.request.FILES['file'], self.request.REQUEST.get('actionset'), self.request.REQUEST.get('action'))
            return build_json_response(dict(path=path))

        def save_recording_upload(self, file, actionset_id, action_uuid):
            flow = self.get_object()
            return default_storage.save('recordings/%d/%d/steps/%s.wav' % (flow.org.pk, flow.id, action_uuid), file)

    class BaseList(FlowActionMixin, OrgQuerysetMixin, OrgPermsMixin, SmartListView):
        title = _("Flows")
        refresh = 10000
        fields = ('name', 'modified_on')
        default_template = 'flows/flow_list.html'
        default_order = ('-modified_on',)
        search_fields = ('name__icontains',)

        def get_context_data(self, **kwargs):
            context = super(FlowCRUDL.BaseList, self).get_context_data(**kwargs)
            context['org_has_flows'] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count()
            context['folders'] = self.get_folders()
            context['labels'] = self.get_flow_labels()
            context['request_url'] = self.request.path
            context['actions'] = self.actions
            return context

        def get_flow_labels(self):
            labels = []
            for label in FlowLabel.objects.filter(org=self.request.user.get_org(), parent=None):
                labels.append(dict(pk=label.pk, label=label.name, count=label.get_flows_count(), children=label.children.all()))
            return labels

        def get_folders(self):
            org = self.request.user.get_org()

            return [
                dict(label="Active", url=reverse('flows.flow_list'), count=Flow.objects.filter(is_active=True, is_archived=False, flow_type=Flow.FLOW, org=org).count()),
                dict(label="Archived", url=reverse('flows.flow_archived'), count=Flow.objects.filter(is_active=True, is_archived=True, org=org).count())
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
            queryset = queryset.filter(is_active=True, is_archived=False).exclude(flow_type=Flow.MESSAGE)
            types = self.request.REQUEST.getlist('flow_type')
            if types:
                queryset = queryset.filter(flow_type__in=types)
            return queryset

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
            if children:
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
                dict(name='contact', display=unicode(_('Contact Name'))),
                dict(name='contact.first_name', display=unicode(_('Contact First Name'))),
                dict(name='contact.groups', display=unicode(_('Contact Groups'))),
                dict(name='contact.language', display=unicode(_('Contact Language'))),
                dict(name='contact.mailto', display=unicode(_('Contact Email Address'))),
                dict(name='contact.name', display=unicode(_('Contact Name'))),
                dict(name='contact.tel', display=unicode(_('Contact Phone'))),
                dict(name='contact.tel_e164', display=unicode(_('Contact Phone - E164'))),
                dict(name='contact.uuid', display=unicode(_("Contact UUID"))),
                dict(name='new_contact', display=unicode(_('New Contact')))
            ]
            contact_variables += [dict(name="contact.%s" % field.key, display=field.label) for field in ContactField.objects.filter(org=org, is_active=True)]

            date_variables = [
                dict(name='date', display=unicode(_('Current Date and Time'))),
                dict(name='date.now', display=unicode(_('Current Date and Time'))),
                dict(name='date.today', display=unicode(_('Current Date'))),
                dict(name='date.tomorrow', display=unicode(_("Tomorrow's Date"))),
                dict(name='date.yesterday', display=unicode(_("Yesterday's Date")))
            ]

            flow_variables = [
                dict(name='channel', display=unicode(_('Sent to'))),
                dict(name='channel.name', display=unicode(_('Sent to'))),
                dict(name='channel.tel', display=unicode(_('Sent to'))),
                dict(name='channel.tel_e164', display=unicode(_('Sent to'))),
                dict(name='step', display=unicode(_('Sent to'))),
                dict(name='step.value', display=unicode(_('Sent to')))
            ]
            flow_variables += [dict(name='step.%s' % v['name'], display=v['display']) for v in contact_variables]
            flow_variables.append(dict(name='flow', display=unicode(_('All flow variables'))))

            flow_id = self.request.REQUEST.get('flow', None)

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
            return build_json_response(dict(message_completions=contact_variables + date_variables + flow_variables,
                                            function_completions=function_completions))

    class Read(OrgObjPermsMixin, SmartReadView):
        def derive_title(self):
            return self.object.name

        def get_context_data(self, *args, **kwargs):

            # hangup any test calls if we have them
            IVRCall.hangup_test_call(self.get_object())

            org = self.request.user.get_org()
            context = super(FlowCRUDL.Read, self).get_context_data(*args, **kwargs)
            flow = self.get_object(self.get_queryset())
            flow.ensure_current_version()

            initial = flow.as_json(expand_contacts=True)
            initial['archived'] = self.object.is_archived
            context['initial'] = json.dumps(initial)
            context['flows'] = Flow.objects.filter(org=org, is_active=True, flow_type__in=[Flow.FLOW, Flow.VOICE], is_archived=False)

            if org:
                languages = org.languages.all().order_by('orgs')
                for lang in languages:
                    if self.get_object().base_language == lang.iso_code:
                        context['base_language'] = lang

                context['languages'] = languages

            contact_fields = [dict(id="name", text="Contact Name")]
            if org:
                for field in org.contactfields.filter(is_active=True):
                    contact_fields.append(dict(id=field.key, text=field.label))
            context['contact_fields'] = json.dumps(contact_fields)

            context['can_edit'] = False

            if self.has_org_perm('flows.flow_json') and not self.request.user.is_superuser:
                context['can_edit'] = True

            # are there pending starts?
            starting = False
            start = self.object.starts.all().order_by('-created_on')
            if start.exists() and start[0].status in [FlowStart.STATUS_STARTING, FlowStart.STATUS_PENDING]:
                starting = True
            context['starting'] = starting

            return context

        def get_gear_links(self):
            links = []

            if self.get_object().flow_type != 'S' \
                    and self.has_org_perm('flows.flow_broadcast') \
                    and not self.get_object().is_archived:

                links.append(dict(title=_("Start Flow"),
                                  style='btn-primary',
                                  js_class='broadcast-rulesflow',
                                  href='#'))

            if self.has_org_perm('flows.flow_results'):
                links.append(dict(title=_("Results"),
                                  style='btn-primary',
                                  href=reverse('flows.flow_results', args=[self.get_object().id])))
            if len(links) > 1:
                links.append(dict(divider=True)),

            if self.has_org_perm('flows.flow_update'):
                links.append(dict(title=_("Edit"),
                                  js_class='update-rulesflow',
                                  href='#'))

            if self.has_org_perm('flows.flow_copy'):
                links.append(dict(title=_("Copy"),
                                  posterize=True,
                                  href=reverse('flows.flow_copy', args=[self.get_object().id])))

            if self.has_org_perm('orgs.org_export'):
                links.append(dict(title=_("Export"),
                                  href='%s?flow=%s' % (reverse('orgs.org_export'), self.get_object().id)))

            if self.has_org_perm('flows.flow_revisions'):
                links.append(dict(divider=True)),
                links.append(dict(title=_("Revision History"),
                                  ngClick='showRevisionHistory()',
                                  href='#'))

            if self.has_org_perm('flows.flow_delete'):
                links.append(dict(divider=True)),
                links.append(dict(title=_("Delete"),
                                  delete=True,
                                  success_url=reverse('flows.flow_list'),
                                  href=reverse('flows.flow_delete', args=[self.get_object().id])))

            return links

    class Editor(Read):
        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Editor, self).get_context_data(*args, **kwargs)

            context['media_url'] = 'https://%s/' % settings.AWS_BUCKET_DOMAIN

            # are there pending starts?
            starting = False
            start = self.object.starts.all().order_by('-created_on')
            if start.exists() and start[0].status in [FlowStart.STATUS_STARTING, FlowStart.STATUS_PENDING]:
                starting = True
            context['starting'] = starting
            context['mutable'] = False
            if self.has_org_perm('flows.flow_update') and not self.request.user.is_superuser:
                context['mutable'] = True

            context['has_airtime_service'] = bool(self.object.org.is_connected_to_transferto())

            return context

        def get_template_names(self):
            return "flows/flow_editor.haml"

    class ExportResults(ModalMixin, OrgPermsMixin, SmartFormView):
        class ExportForm(forms.Form):
            flows = forms.ModelMultipleChoiceField(Flow.objects.filter(id__lt=0), required=True,
                                                   widget=forms.MultipleHiddenInput())
            contact_fields = forms.ModelMultipleChoiceField(ContactField.objects.filter(id__lt=0), required=False,
                                                            help_text=_("Which contact fields, if any, to include "
                                                                        "in the export"))
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

                if 'contact_fields' in cleaned_data and len(cleaned_data['contact_fields']) > 10:
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
            if flow_ids:
                return dict(flows=Flow.objects.filter(org=self.request.user.get_org(), is_active=True,
                                                      id__in=flow_ids.split(',')))
            else:
                return dict()

        def form_valid(self, form):
            analytics.track(self.request.user.username, 'temba.flow_exported')

            user = self.request.user
            org = user.get_org()

            # is there already an export taking place?
            existing = ExportFlowResultsTask.objects.filter(org=org, is_finished=False,
                                                            created_on__gt=timezone.now() - timedelta(hours=24))\
                                                    .order_by('-created_on').first()

            # if there is an existing export, don't allow it
            if existing:
                messages.info(self.request,
                              _("There is already an export in progress, started by %s. You must wait "
                                "for that export to complete before starting another." % existing.created_by.username))
            else:
                export = ExportFlowResultsTask.create(org, user, form.cleaned_data['flows'],
                                                      contact_fields=form.cleaned_data['contact_fields'],
                                                      include_runs=form.cleaned_data['include_runs'],
                                                      include_msgs=form.cleaned_data['include_messages'],
                                                      responded_only=form.cleaned_data['responded_only'])
                export_flow_results_task.delay(export.pk)

                if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):
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

    class Results(FormaxMixin, OrgObjPermsMixin, SmartReadView):

        class RemoveRunForm(forms.Form):
            run = forms.ModelChoiceField(FlowRun.objects.filter(pk__lt=0))

            def __init__(self, flow, *args, **kwargs):
                self.flow = flow
                super(FlowCRUDL.Results.RemoveRunForm, self).__init__(*args, **kwargs)

                self.fields['run'].queryset = FlowRun.objects.filter(flow=flow)

            def execute(self):
                data = self.cleaned_data
                run = data['run']

                # remove this run and its steps in the process
                run.delete()
                return dict(status="success")

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm('flows.flow_broadcast'):
                if 'json' not in self.request.REQUEST:
                    formax.add_section('broadcast', reverse('flows.flow_broadcast', args=[self.object.pk]),
                                       'icon-users', action='readonly')

        def post(self, request, *args, **kwargs):
            form = FlowCRUDL.Results.RemoveRunForm(self.get_object(), self.request.POST)
            if not self.has_org_perm('flows.flow_delete'):
                return HttpResponse(json.dumps(dict(error=_("Sorry you have no permission for this action."))))

            if form.is_valid():
                result = form.execute()
                return HttpResponse(json.dumps(result))

            # can happen when there are double clicks, just ignore it
            else:  # pragma: no cover
                return HttpResponse(json.dumps(dict(status="success")))

        def render_to_response(self, context, **response_kwargs):
            org = self.request.user.get_org()

            if 'json' in self.request.REQUEST:
                start = int(self.request.REQUEST.get('iDisplayStart', 0))
                show = int(self.request.REQUEST.get('iDisplayLength', 20))

                sort_col = int(self.request.REQUEST.get('iSortCol_0', 0))
                sort_direction = self.request.REQUEST.get('sSortDir_0', 'desc')

                # create mapping of uuid to column
                col_map = dict()
                idx = 0
                for col in self.request.REQUEST.get('cols', '').split(','):
                    col_map[col] = idx
                    idx += 1

                runs = FlowRun.objects.filter(flow=self.object).exclude(contact__is_test=True)

                query = self.request.REQUEST.get('sSearch', None)
                if query:
                    if org.is_anon:
                        # try casting our query to an int if they are querying by contact id
                        query_int = -1
                        try:
                            query_int = int(query)
                        except Exception:
                            pass

                        runs = runs.filter(Q(contact__name__icontains=query) | Q(contact__id=query_int))
                    else:
                        runs = runs.filter(Q(contact__name__icontains=query) | Q(contact__urns__path__icontains=query))

                if org.is_anon:
                    runs = runs.values('contact__pk', 'contact__name')
                else:
                    runs = runs.values('contact__pk', 'contact__name', 'contact__urns__path')

                runs = runs.annotate(count=Count('pk'), started=Max('created_on'))\

                initial_sort = 'started'
                if sort_col == 1:
                    if org.is_anon:
                        initial_sort = 'contact__id'
                    else:
                        initial_sort = 'contact__urns__path'
                elif sort_col == 2:
                    initial_sort = 'contact__name'
                elif sort_col == 3:
                    initial_sort = 'count'

                if sort_direction == 'desc':
                    initial_sort = "-%s" % initial_sort
                runs = runs.order_by(initial_sort, '-count')

                total = runs.count()

                runs = runs[start:(start + show)]

                # fetch the step data for our set of contacts
                contacts = []
                for run in runs:
                    contacts.append(run['contact__pk'])

                steps = FlowStep.objects.filter(run__flow=self.object, run__contact__in=contacts).exclude(rule_value=None)
                steps = steps.order_by('run__contact__pk', 'step_uuid', '-arrived_on').distinct('run__contact__pk', 'step_uuid')
                steps = steps.prefetch_related('messages', 'broadcasts')

                # now create an nice table for them
                contacts = dict()

                for step in steps:
                    contact_pk = step.run.contact.pk
                    if contact_pk in contacts:
                        contact_steps = contacts[contact_pk]
                    else:
                        contact_steps = ['' for i in range(len(col_map))]

                    if step.step_uuid in col_map:
                        contact_steps[col_map[step.step_uuid]] = dict(value=step.rule_value, category=step.rule_category, text=step.get_text())
                    contacts[contact_pk] = contact_steps

                rows = []
                for run in runs:

                    cols = [dict() for i in range(len(col_map))]
                    if run['contact__pk'] in contacts:
                        cols = contacts[run['contact__pk']]
                    name = ""
                    if run['contact__name']:
                        name = run['contact__name']

                    date = run['started']
                    date = timezone.localtime(date).strftime('%b %d, %Y %I:%M %p').lstrip("0").replace(" 0", " ")

                    phone = "%010d" % run['contact__pk']
                    if not org.is_anon:
                        phone = run['contact__urns__path']

                    rows.append([date,
                                 dict(category=phone, contact=run['contact__pk']),
                                 dict(category=name),
                                 dict(contact=run['contact__pk'], category=run['count'])] + cols)

                return build_json_response(dict(iTotalRecords=total, iTotalDisplayRecords=total, sEcho=self.request.REQUEST.get('sEcho', 0), aaData=rows))
            else:
                if 'c' in self.request.REQUEST:
                    contact = Contact.objects.filter(pk=self.request.REQUEST['c'], org=self.object.org, is_active=True)
                    if contact:
                        contact = contact[0]
                        runs = list(contact.runs.filter(flow=self.object).order_by('-created_on'))
                        for run in runs:
                            # step_uuid__in=step_uuids
                            run.__dict__['messages'] = list(Msg.all_messages.filter(steps__run=run).order_by('created_on'))
                        context['runs'] = runs
                        context['contact'] = contact

                return super(FlowCRUDL.Results, self).render_to_response(context, **response_kwargs)

        def get_template_names(self):
            if 'c' in self.request.REQUEST:
                return ['flows/flow_results_contact.haml']
            else:
                return super(FlowCRUDL.Results, self).get_template_names()

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Results, self).get_context_data(*args, **kwargs)
            return context

    class Activity(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            flow = self.get_object(self.get_queryset())

            # if we are interested in the flow details add that
            flow_json = dict()
            if request.REQUEST.get('flow', 0):
                flow_json = flow.as_json()

            # get our latest start, we might warn the user that one is in progress
            start = flow.starts.all().order_by('-created_on')
            pending = None
            if start.count() and (start[0].status == FlowStart.STATUS_STARTING or start[0].status == FlowStart.STATUS_PENDING):
                pending = start[0].status

            # if we have an active call, include that
            from temba.ivr.models import IVRCall

            messages = []
            call = IVRCall.objects.filter(contact__is_test=True, flow=flow).first()
            if call:
                call = dict(pk=call.pk,
                            call_type=call.call_type,
                            status=call.status,
                            duration=call.get_duration(),
                            number=call.contact.raw_tel())

                messages = Msg.current_messages.filter(contact=Contact.get_test_contact(self.request.user)).order_by('created_on')
                action_logs = list(ActionLog.objects.filter(run__flow=flow, run__contact__is_test=True).order_by('created_on'))

                messages_and_logs = chain(messages, action_logs)
                messages_and_logs = sorted(messages_and_logs, cmp=msg_log_cmp)

                messages_json = []
                if messages_and_logs:
                    for msg in messages_and_logs:
                        messages_json.append(msg.as_json())
                messages = messages_json

            (active, visited) = flow.get_activity()

            return build_json_response(dict(call=call, messages=messages,
                                            activity=active,
                                            visited=visited,
                                            flow=flow_json, pending=pending))

    class Simulate(OrgObjPermsMixin, SmartReadView):

        def get(self, request, *args, **kwargs):
            return HttpResponseRedirect(reverse('flows.flow_editor', args=[self.get_object().pk]))

        def post(self, request, *args, **kwargs):

            # try to parse our body
            try:
                json_dict = json.loads(request.body)
            except Exception as e:
                return build_json_response(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            Contact.set_simulation(True)
            user = self.request.user
            test_contact = Contact.get_test_contact(user)

            analytics.track(user.username, 'temba.flow_simulated')

            flow = self.get_object(self.get_queryset())

            if json_dict and json_dict.get('hangup', False):
                # hangup any test calls if we have them
                IVRCall.hangup_test_call(self.get_object())
                return build_json_response(dict(status="success", message="Test call hung up"))

            if json_dict and json_dict.get('has_refresh', False):

                lang = request.REQUEST.get('lang', None)
                if lang:
                    test_contact.language = lang
                    test_contact.save()

                # delete all our steps and messages to restart the simulation
                runs = FlowRun.objects.filter(contact=test_contact)
                steps = FlowStep.objects.filter(run__in=runs)

                ActionLog.objects.filter(run__in=runs).delete()
                Msg.current_messages.filter(contact=test_contact).delete()
                IVRCall.objects.filter(contact=test_contact).delete()

                runs.delete()
                steps.delete()

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

            from temba.settings import TEMBA_HOST, STATIC_URL
            media_url = 'http://%s%simages' % (TEMBA_HOST, STATIC_URL)

            if 'new_photo' in json_dict:
                media = '%s/png:%s/simulator_photo.png' % (Msg.MEDIA_IMAGE, media_url)
            elif 'new_gps' in json_dict:
                media = '%s:47.6089533,-122.34177' % Msg.MEDIA_GPS
            elif 'new_video' in json_dict:
                media = '%s/mp4:%s/simulator_video.mp4' % (Msg.MEDIA_VIDEO, media_url)
            elif 'new_audio' in json_dict:
                media = '%s/mp4:%s/simulator_audio.m4a' % (Msg.MEDIA_AUDIO, media_url)

            if new_message or media:
                status = PENDING
                if new_message == "__interrupt__":
                    status = INTERRUPTED
                try:
                    Msg.create_incoming(None,
                                        test_contact.get_urn(TEL_SCHEME).urn,
                                        new_message,
                                        media=media,
                                        org=user.get_org(),
                                        status=status)
                except Exception as e:
                    traceback.print_exc(e)
                    return build_json_response(dict(status="error", description="Error creating message: %s" % str(e)), status=400)

            messages = Msg.current_messages.filter(contact=test_contact).order_by('pk', 'created_on')
            action_logs = ActionLog.objects.filter(run__contact=test_contact).order_by('pk', 'created_on')

            messages_and_logs = chain(messages, action_logs)
            messages_and_logs = sorted(messages_and_logs, cmp=msg_log_cmp)

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

            return build_json_response(dict(status="success", description="Message sent to Flow", **response))

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
            except Exception:
                logger.error('Unable to get currency for channel countries.', exc_info=True)
                channel_countries = []

            # all the channels available for our org
            channels = [dict(uuid=chan.uuid, name=u"%s: %s" % (chan.get_channel_type_display(), chan.get_address_display())) for chan in flow.org.channels.filter(is_active=True)]
            return build_json_response(dict(flow=flow.as_json(expand_contacts=True), languages=languages,
                                            channel_countries=channel_countries, channels=channels))

        def post(self, request, *args, **kwargs):

            # require update permissions
            if not self.has_org_perm('flows.flow_update'):
                return HttpResponseRedirect(reverse('flows.flow_json', args=[self.get_object().pk]))

            # try to parse our body
            json_string = request.body

            analytics.track(self.request.user.username, 'temba.flow_updated')

            # try to save the our flow, if this fails, let's let that bubble up to our logger
            json_dict = json.loads(json_string)
            print json.dumps(json_dict, indent=2)

            try:
                response_data = self.get_object(self.get_queryset()).update(json_dict, user=self.request.user)
                return build_json_response(response_data, status=200)
            except Exception as e:
                # give the editor a formatted error response
                return build_json_response(dict(status="failure", description=str(e)), status=400)

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

            def clean_omnibox(self):
                starting = self.cleaned_data['omnibox']
                if not starting['groups'] and not starting['contacts']:
                    raise ValidationError(_("You must specify at least one contact or one group to start a flow."))

                return starting

            def clean(self):
                cleaned = super(FlowCRUDL.Broadcast.BroadcastForm, self).clean()

                # check whether there are any flow starts that are incomplete
                if FlowStart.objects.filter(flow=self.flow).exclude(status__in=[FlowStart.STATUS_COMPLETE, FlowStart.STATUS_FAILED]):
                    raise ValidationError(_("This flow is already being started, please wait until that process is complete before starting more contacts."))

                if self.flow.org.is_suspended():
                    raise ValidationError(_("Sorry, your account is currently suspended. To enable sending messages, please contact support."))

                return cleaned

            class Meta:
                model = Flow
                fields = ('omnibox', 'restart_participants')

        form_class = BroadcastForm
        fields = ('omnibox', 'restart_participants')
        success_message = ''
        submit_button_name = _("Add Contacts to Flow")
        success_url = 'id@flows.flow_editor'

        def get_context_data(self, *args, **kwargs):
            context = super(FlowCRUDL.Broadcast, self).get_context_data(*args, **kwargs)
            context['run_count'] = self.object.get_total_runs()
            context['complete_count'] = self.object.get_completed_runs()
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
                             restart_participants=form.cleaned_data['restart_participants'])
            return flow


# this is just for adhoc testing of the preprocess url
class PreprocessTest(FormView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(PreprocessTest, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse(json.dumps(dict(text='Norbert', extra=dict(occupation='hoopster', skillz=7.9))),
                            content_type='application/javascript')


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
            if self.form.cleaned_data['flows']:
                flow_ids = [int(f) for f in self.form.cleaned_data['flows'].split(',') if f.isdigit()]

            flows = Flow.objects.filter(org=obj.org, is_active=True, pk__in=flow_ids)

            if flows:
                obj.toggle_label(flows, add=True)

            return obj
