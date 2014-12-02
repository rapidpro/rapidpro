from __future__ import unicode_literals

import json
import re
import time

from datetime import datetime
from django.conf import settings
from django.core.cache import cache
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
from smartmin.views import SmartCRUDL, SmartCreateView, SmartReadView, SmartListView, SmartUpdateView, SmartDeleteView, SmartTemplateView
from temba.contacts.fields import OmniboxField
from temba.contacts.models import Contact, ContactGroup, ContactField, TEL_SCHEME
from temba.formax import FormaxMixin
from temba.ivr.models import IVRCall
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.reports.models import Report
from temba.flows.models import Flow, FlowReferenceException, FlowRun, STARTING, PENDING
from temba.flows.tasks import export_flow_results_task
from temba.msgs.models import Msg
from temba.msgs.views import BaseActionForm
from temba.triggers.models import Trigger, KEYWORD_TRIGGER
from temba.utils import analytics, build_json_response
from temba.values.models import Value
from .models import FlowStep, RuleSet, ActionLog, ExportFlowResultsTask, FlowLabel, COMPLETE, FAILED, FlowStart


def flow_unread_response_count_processor(request):
    """
    Context processor to calculate the number of unread responses on flows so we can
    display that in the menu.
    """
    context = dict()
    user = request.user

    if user.is_superuser or user.is_anonymous():
        return context

    org = user.get_org()

    if org:
        flows_last_viewed = org.flows_last_viewed
        unread_response_count = Flow.get_org_responses_since(org, flows_last_viewed)

        if request.path.find("/flow/") == 0:
            org.flows_last_viewed = timezone.now()
            org.save()
            unread_response_count = 0

        context['flows_last_viewed'] = flows_last_viewed
        context['flows_unread_count'] = unread_response_count

    return context


class BaseFlowForm(forms.ModelForm):
    expires_after_minutes = forms.ChoiceField(label=_('Expire inactive contacts'),
                                              help_text=_("When inactive contacts should be removed from the flow"),
                                              initial=str(60*24*7),
                                              choices=((0, _('Never')),
                                                       (5, _('After 5 minutes')),
                                                       (30, _('After 30 minutes')),
                                                       (60, _('After 1 hour')),
                                                       (60*3, _('After 3 hours')),
                                                       (60*6, _('After 6 hours')),
                                                       (60*12, _('After 12 hours')),
                                                       (60*24, _('After 1 day')),
                                                       (60*24*3, _('After 3 days')),
                                                       (60*24*7, _('After 1 week')),
                                                       (60*24*14, _('After 2 weeks')),
                                                       (60*24*30, _('After 30 days'))))

    def clean_keyword_triggers(self):
        org = self.user.get_org()
        wrong_format = []
        existing_keywords = []
        keyword_triggers = self.cleaned_data.get('keyword_triggers', '').strip()

        for keyword in keyword_triggers.split(','):
            if keyword and not re.match('^\w+$', keyword, flags=re.UNICODE):
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

class FlowActionForm(BaseActionForm):
    ALLOWED_ACTIONS = (('archive', _("Archive Flows")),
                       ('label', _("Label Messages")),
                       ('restore', _("Restore Flows")))

    OBJECT_CLASS = Flow
    LABEL_CLASS = FlowLabel
    HAS_IS_ACTIVE = True

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
            latest_only = self.request.GET.get('latest_only', 'true') == 'true'

            ruleset = self.get_object()
            results = Value.get_value_summary(ruleset=ruleset, filters=filters, segment=segment, latest_only=latest_only)
            return dict(id=ruleset.pk, label=ruleset.label, results=results)

        def render_to_response(self, context, **response_kwargs):
            response = HttpResponse(json.dumps(context), content_type='application/javascript')
            return response

    class Choropleth(OrgPermsMixin, SmartReadView):

        def get_context_data(self, **kwargs):
            from temba.values.models import Value
            from temba.locations.models import AdminBoundary

            context = dict()

            country = self.derive_org().country
            parentOsmId = self.request.GET.get('boundary', country.osm_id)
            parent = AdminBoundary.objects.get(osm_id=parentOsmId)

            filters = json.loads(self.request.GET.get('filters', '[]'))
            filtering_categories = list()

            for filter in filters:
                if 'ruleset' in filter and filter['ruleset'] == self.get_object().pk:
                    for category in filter['categories']:
                        filtering_categories.append(category)

            # get the rules for this ruleset
            # TODO: this should actually merge across all rules, we might set the field in two different places
            flow = self.get_object().flow
            rules = self.get_object().get_rules()
            category_order = list()

            all_uuid_to_category = dict()

            # first build a map of uuid->category based on the values we have (this will give us category names for
            # uuid's that don't exist anymore)
            for value in Value.objects.filter(ruleset=self.get_object()).distinct('rule_uuid', 'category'):
                all_uuid_to_category[value.rule_uuid] = value.category

            # now override any of those based on the current set of rules, these take precedence
            for rule in rules:
                rule_category = rule.get_category_name(flow.base_language)

                # this is n*n, but for a really small n, we want to preserve order
                if rule_category != 'Other' and rule_category not in category_order:
                    category_order.append(rule_category)

                # overwrite any category for this
                all_uuid_to_category[rule.uuid] = rule_category

            # trim our uuids to only those that have categories we care about
            uuid_to_category = dict()
            valid_uuids = list()
            for uuid, category in all_uuid_to_category.items():
                if category in category_order:
                    uuid_to_category[uuid] = category
                    valid_uuids.append(uuid)

            if not filters:
                filtering_categories = uuid_to_category.values()

            filtering_categories_uuids = [k for k,v in uuid_to_category.items() if v in filtering_categories]

            # if there are more than two rules, then we need to determine the most popular
            # cross it against all others
            if len(category_order) > 2:
                category_counts = dict()
                uuid_counts = Value.objects.filter(ruleset=self.get_object()).values('rule_uuid').annotate(Count('rule_uuid'))

                for uuid_count in uuid_counts:
                    category = uuid_to_category.get(uuid_count['rule_uuid'], None)
                    if category:
                        if not category in category_counts:
                            category_counts[category] = uuid_count['rule_uuid__count']
                        else:
                            category_count = category_counts[category]
                            category_counts[category] = uuid_count['rule_uuid__count'] + category_count


                # get the most popular category
                pop_category = category_order[0]
                pop_category_count = 0
                for category, count in category_counts.items():
                    if count > pop_category_count:
                        pop_category = category
                        pop_category_count = count

                # now remap our uuids to be only care about our popular category and everything else
                category_order = [pop_category, unicode(_("Others"))]

                remapped_uuid_to_category = dict()
                for uuid, category in uuid_to_category.items():
                    if category != pop_category:
                        category = unicode(_("Others"))

                    remapped_uuid_to_category[uuid] = category

                uuid_to_category = remapped_uuid_to_category

            # for each boundary
            boundary_scores = dict()
            country_total_count = 0

            for boundary in AdminBoundary.objects.filter(parent=parent).order_by('-name'):
                # get our category counts
                uuid_counts = Value.objects.filter(ruleset=self.get_object(), contact__values__location_value=boundary)
                uuid_counts = uuid_counts.filter(rule_uuid__in=valid_uuids).values('rule_uuid').annotate(Count('rule_uuid'))

                boundary_categories = dict()

                # for each uuid, sum it up for the category
                for uuid_count in uuid_counts:
                    category = uuid_to_category[uuid_count['rule_uuid']]
                    category_stats = boundary_categories.get(category, dict(count=0))
                    boundary_categories[category] = category_stats
                    category_stats['count'] = category_stats['count'] + uuid_count['rule_uuid__count']
                    category_stats['count_for_totals'] = category_stats['count']

                    if uuid_count['rule_uuid'] not in filtering_categories_uuids:
                        category_stats['count'] = 0

                total_count = sum([s['count_for_totals'] for s in boundary_categories.values()])
                country_total_count += total_count

                category_stats = boundary_categories.get(category_order[0], dict(count=0))
                count = category_stats['count']
                boundary_score = (count / (total_count * 1.0)) if total_count > 0 else 0

                # build a list of our category results
                results = []
                for category in category_order:
                    count = boundary_categories.get(category, dict(count=0))['count']
                    percentage = int(round(count * 100.0 / total_count)) if count else 0

                    results.append(dict(label=category, count=count, percentage=percentage))

                boundary_scores[boundary] = dict(name=boundary.name, score=boundary_score, count=total_count, results=results)

            points = []
            osm_to_score = dict()
            for (boundary, scores) in boundary_scores.items():
                osm_to_score[boundary.osm_id] = boundary_scores[boundary]
                points.append(boundary_scores[boundary]['score'])

            from temba.flows.stats import get_jenks_breaks
            breaks = get_jenks_breaks(sorted(points), 11)

            breaks = [.2, .3, .35, .40, .45, .55, .60, .65, .7, .8, 1]

            # calculate totals for entire country
            total_category_results = dict()
            for score in boundary_scores.values():
                for result in score['results']:
                    category = result['label']
                    count = total_category_results.get(category, 0) + result['count']
                    total_category_results[category] = count

            total_results = []

            for c in category_order:
                count = total_category_results[c]
                total_results.append(dict(count=count, label=c, percentage=int(round(count * 100.0 / country_total_count)) if count else 0))

            context['breaks'] = breaks
            context['scores'] = osm_to_score
            context['categories'] = category_order
            context['totals'] = dict(name=parent.name, count=country_total_count, results=total_results)

            return context

    class Analytics(OrgPermsMixin, SmartTemplateView):
        title = "Analytics"

        def get_context_data(self, **kwargs):
            org = self.request.user.get_org()
            rule_ids = [r.uuid for r in RuleSet.objects.filter(flow__is_active=True, flow__org=org).order_by('uuid')]

            # create a lookup table so we can augment our values below
            rule_stats = {}
            for rule_id in rule_ids:
                rule = FlowStep.objects.filter(step_uuid=rule_id).exclude(rule_category=None).values('step_uuid').annotate(contacts=Count('contact', distinct=True),
                                                                                                                           categories=Count('rule_category', distinct=True),
                                                                                                                           last_message=Max('arrived_on'),
                                                                                                                           message_count=Count('pk'))

                if rule:
                    rule = rule[0]
                    rule_stats[rule_id] = dict(categories=rule['categories'], messages=rule['message_count'], contacts=rule['contacts'], last_message=rule['last_message'])

            flows = Flow.objects.filter(is_active=True, org=self.request.user.get_org()).order_by('name')
            flow_json = []
            for flow in flows:
                rules = flow.rule_sets.all().order_by('y').exclude(label=None)

                # aggregate our flow status from the rule sets
                # note that flow_contacts is a summation across unique contacts
                # at each rule which is pretty meaningless except as a proxy
                flow_messages = 0
                flow_contacts = 0
                flow_categories = 0
                flow_last = None

                children = []
                for rule in rules:
                    rule_dict = dict(text=rule.label, id=rule.pk, flow=flow.pk)
                    if rule.uuid in rule_stats:
                        rule_dict['stats'] = rule_stats[rule.uuid]
                    else:
                        rule_dict['stats'] = dict(categories=0, messages=0, contacts=0, last_message=None)

                    flow_messages += rule_dict['stats']['messages']
                    flow_contacts += rule_dict['stats']['contacts']
                    flow_categories += rule_dict['stats']['categories']

                    if not flow_last or rule_dict['stats']['last_message'] and rule_dict['stats']['last_message'] > flow_last:
                        flow_last = rule_dict['stats']['last_message']
                    children.append(rule_dict)

                if rules:
                    flow_json.insert(len(flow_json) - len(rules), dict(text=flow.name, id=flow.pk, rules=children,
                                                                       stats=dict(categories=flow_categories, messages=flow_messages, contacts=flow_contacts, last_message=flow_last)))

            dthandler = lambda obj: obj.isoformat() if isinstance(obj, datetime) else obj

            groups = ContactGroup.objects.filter(is_active=True, org=org).order_by('name').prefetch_related('contacts')
            groups_json = []
            for group in groups:
                if group.contacts.exists():
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

            return dict(flows=json.dumps(flow_json, default=dthandler), groups=json.dumps(groups_json), reports=json.dumps(reports_json), current_report=current_report)


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
    actions = ('list', 'archived', 'copy', 'create', 'delete', 'update', 'export', 'simulate', 'export_results', 'upload_action_recording',
               'read', 'editor', 'results', 'json', 'broadcast', 'activity', 'filter', 'completion', 'versions')

    model = Flow

    class Versions(OrgObjPermsMixin, SmartReadView):
        def get(self, request, *args, **kwargs):
            flow = self.get_object()
            versions = [version.as_json() for version in flow.versions.all().order_by('-created_on')[:25]]
            return build_json_response(versions)

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
                                          help_text=_('Place a phone call or use text messaging'),
                                          choices=((Flow.FLOW, 'Text Messaging'),
                                                   (Flow.VOICE, 'Phone Call')))

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.Create.FlowCreateForm, self).__init__(*args, **kwargs)
                self.user = user

                self.fields['base_language'] = forms.ChoiceField(label=_('Language'), initial=self.user.get_org().primary_language,
                    choices=((lang.iso_code, lang.name) for lang in self.user.get_org().languages.all().order_by('orgs', 'name')))

            class Meta:
                model = Flow

        form_class = FlowCreateForm
        fields = ('name', 'keyword_triggers', 'expires_after_minutes')
        success_url = 'id@flows.flow_editor'
        success_message = ''
        field_config = dict(name=dict(help=_("Choose a name to describe this flow, e.g. Demographic Survey")))

        def derive_fields(self):
            fields = self.fields
            org = self.request.user.get_org()

            if org.primary_language:
                fields += ('base_language',)

            if org.supports_ivr():
                fields += ('flow_type',)

            return fields

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super(FlowCRUDL.Create, self).get_context_data(**kwargs)
            context['has_flows'] = Flow.objects.filter(org=self.request.user.get_org(), is_active=True).count() > 0
            return context

        def pre_save(self, obj):
            analytics.track(self.request.user.username, 'temba.flow_created', dict(name=obj.name))

            obj = super(FlowCRUDL.Create, self).pre_save(obj)
            user = self.request.user
            obj.org = user.get_org()
            obj.saved_by = user
            return obj

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

        def pre_delete(self, object):
            # remove our ivr recordings if we have any
            if object.flow_type == 'V':
                try:
                    path = 'recordings/%d/%d' % (object.org.pk, object.pk)
                    if default_storage.exists(path):
                        default_storage.delete(path)
                except:
                    pass

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
            keyword_triggers = forms.CharField(required=False, label=_("Global keyword triggers"),
                                               help_text=_("When a user sends any of these keywords they will begin this flow"))

            def __init__(self, user, *args, **kwargs):
                super(FlowCRUDL.Update.FlowUpdateForm, self).__init__(*args, **kwargs)
                self.user = user

                flow_triggers = Trigger.objects.filter(org=self.instance.org, flow=self.instance, is_archived=False, groups=None,
                                                       trigger_type=KEYWORD_TRIGGER).order_by('created_on')

                # if we don't have a base language let them pick one (this is immutable)
                if not self.instance.base_language:
                    choices = [('', 'No Preference')]
                    choices += [(lang.iso_code, lang.name) for lang in self.instance.org.languages.all().order_by('orgs', 'name')]
                    self.fields['base_language'] = forms.ChoiceField(label=_('Language'), choices=choices)

                self.fields['keyword_triggers'].initial = ','.join([t.keyword for t in flow_triggers])

            class Meta:
                model = Flow
                fields = ('name', 'keyword_triggers', 'labels', 'base_language', 'expires_after_minutes', 'ignore_triggers')

        success_message = ''
        fields = ('name', 'keyword_triggers', 'expires_after_minutes', 'ignore_triggers')
        form_class = FlowUpdateForm

        def derive_fields(self):
            fields = [field for field in self.fields]
            if not self.get_object().base_language and self.org.primary_language:
                fields += ['base_language']
            return fields

        def get_form_kwargs(self):
            kwargs = super(FlowCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def pre_save(self, obj):
            # if they are setting a base_language for the first time, update our flow accordingly
            if obj.base_language:
                obj.update_base_language()
            return obj

        def post_save(self, obj):
            keywords = set()
            user = self.request.user
            org = user.get_org()
            existing_keywords = set(t.keyword for t in obj.triggers.filter(org=org, flow=obj, is_archived=False, groups=None))

            if len(self.form.cleaned_data['keyword_triggers']) > 0:
                keywords = set(self.form.cleaned_data['keyword_triggers'].split(','))

            removed_keywords = existing_keywords.difference(keywords)
            for keyword in removed_keywords:
                obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None, is_archived=False).update(is_archived=True)

            added_keywords = keywords.difference(existing_keywords)
            archived_keywords = [t.keyword for t in obj.triggers.filter(org=org, flow=obj, is_archived=True, groups=None)]
            for keyword in added_keywords:
                # first check if the added keyword is not amongst archived
                if keyword in archived_keywords:
                    obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None).update(is_archived=False)
                else:
                    Trigger.objects.create(org=org, keyword=keyword, flow=obj, created_by=user, modified_by=user)

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
            context['org_has_flows'] = Flow.objects.filter(org=self.request.user.get_org()).count()
            context['folders']= self.get_folders()
            context['labels']= self.get_flow_labels()
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

            folders = []
            folders.append(dict(label="Active", url=reverse('flows.flow_list'), count=Flow.objects.filter(is_active=True, is_archived=False, flow_type=Flow.FLOW, org=org).count()))
            folders.append(dict(label="Archived", url=reverse('flows.flow_archived'), count=Flow.objects.filter(is_active=True, is_archived=True, org=org).count()))
            return folders

    class Archived(BaseList):
        actions = ('restore',)

        def derive_queryset(self, *args, **kwargs):
            return super(FlowCRUDL.Archived, self).derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Flows")
        actions = ('archive', 'label')

        def derive_queryset(self, *args, **kwargs):
            return super(FlowCRUDL.List, self).derive_queryset(*args, **kwargs).filter(is_active=True,
                                                                                       is_archived=False).exclude(flow_type=Flow.MESSAGE)

    class Filter(BaseList):
        add_button = True
        actions = ['unlabel', 'label']

        def get_gear_links(self):
            links = []
            run_id = self.request.REQUEST.get('run', None)

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
                return [ _ for _ in  FlowLabel.objects.filter(parent=label)] + [label]

            else:
                return [ label ]

        def get_queryset(self, **kwargs):
            qs = super(FlowCRUDL.Filter, self).get_queryset(**kwargs)
            qs = qs.filter(org=self.request.user.get_org()).order_by('-created_on')
            qs = qs.filter(labels__in=self.get_label_filter(), is_archived=False).distinct().select_related('contact')

            return qs

    class Completion(OrgPermsMixin, SmartListView):
        def render_to_response(self, context, **response_kwargs):

            org = self.request.user.get_org()

            contact_variables = [
                dict(name='new_contact', display=unicode(_('New Contact'))),
                dict(name='contact', display=unicode(_('Contact Name'))),
                dict(name='contact.name', display=unicode(_('Contact Name'))),
                dict(name='contact.first_name', display=unicode(_('Contact First Name'))),
                dict(name='contact.tel', display=unicode(_('Contact Phone'))),
                dict(name='contact.tel_e164', display=unicode(_('Contact Phone - E164'))),
                dict(name='contact.uuid', display=unicode(_("Contact UUID"))),
                dict(name='contact.groups', display=unicode(_('Contact Groups'))),
            ]
            contact_variables += [dict(name="contact.%s" % field.key, display=field.label) for field in ContactField.objects.filter(org=org, is_active=True)]

            date_variables = [
                dict(name='date', display=unicode(_('Current Date and Time'))),
                dict(name='date.now', display=unicode(_('Current Date and Time'))),
                dict(name='date.yesterday', display=unicode(_("Yesterday's Date"))),
                dict(name='date.today', display=unicode(_('Current Date'))),
                dict(name='date.tomorrow', display=unicode(_("Tomorrow's Date")))
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

            return build_json_response(contact_variables + date_variables + flow_variables)

    class Read(OrgObjPermsMixin, SmartReadView):
        def derive_title(self):
            return self.object.name

        def get_context_data(self, *args, **kwargs):

            # hangup any test calls if we have them
            IVRCall.hangup_test_call(self.get_object())

            org = self.request.user.get_org()
            context = super(FlowCRUDL.Read, self).get_context_data(*args, **kwargs)
            initial = self.get_object(self.get_queryset()).as_json(expand_contacts=True)
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
            if start.exists() and start[0].status in [STARTING, PENDING]:
                starting = True
            context['starting'] = starting

            return context

        def get_gear_links(self):
            links = []

            if self.has_org_perm('flows.flow_broadcast') and not self.get_object().is_archived:
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

            if self.has_org_perm('flows.flow_export'):
                links.append(dict(title=_("Export"),
                                  href=reverse('flows.flow_export', args=[self.get_object().id])))


            if self.has_org_perm('flows.flow_versions'):
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
            context = super(FlowCRUDL.Read, self).get_context_data(*args, **kwargs)

            context['recording_url'] = 'http://%s/' % settings.AWS_STORAGE_BUCKET_NAME

            # are there pending starts?
            starting = False
            start = self.object.starts.all().order_by('-created_on')
            if start.exists() and start[0].status in [STARTING, PENDING]:
                starting = True
            context['starting'] = starting
            context['mutable'] = False
            if self.has_org_perm('flows.flow_json') and not self.request.user.is_superuser:
                context['mutable'] = True

            return context

        def get_template_names(self):
            return "flows/flow_editor.haml"

    class Export(OrgPermsMixin, SmartReadView):

        def derive_title(self):
            return _("Export Flow")

        def render_to_response(self, context, **response_kwargs):
            try:
                flow = self.get_object()
                definition = Flow.export_definitions(flows=[flow], fail_on_dependencies=True)
                response = HttpResponse(json.dumps(definition, indent=2), content_type='application/javascript')
                response['Content-Disposition'] = 'attachment; filename=%s.json' % slugify(flow.name)
                return response
            except FlowReferenceException as e:
                context['other_flow_names'] = e.flow_names
                return super(FlowCRUDL.Export, self).render_to_response(context, **response_kwargs)

    class ExportResults(OrgQuerysetMixin, OrgPermsMixin, SmartListView):

        def derive_queryset(self, *args, **kwargs):
            queryset = super(FlowCRUDL.ExportResults, self).derive_queryset(*args, **kwargs)
            return queryset.filter(pk__in=self.request.REQUEST['ids'].split(','))

        def render_to_response(self, context, *args, **kwargs):
            analytics.track(self.request.user.username, 'temba.flow_exported')

            host = self.request.branding['host']
            export = ExportFlowResultsTask.objects.create(created_by=self.request.user, modified_by=self.request.user, host=host)
            for flow in self.get_queryset().order_by('created_on'):
                export.flows.add(flow)
            export_flow_results_task.delay(export.pk)

            from django.contrib import messages
            if not getattr(settings, 'CELERY_ALWAYS_EAGER', False):
                messages.info(self.request, _("We are preparing your export. ") +
                                            _("We will e-mail you at %s when it is ready.") % self.request.user.username)

            else:
                export = ExportFlowResultsTask.objects.get(id=export.pk)
                dl_url = "file://%s/%s" % (settings.MEDIA_ROOT, export.filename)
                messages.info(self.request, _("Export complete, you can find it here: %s (production users will get an email)") % dl_url)

            return HttpResponseRedirect(reverse('flows.flow_list'))

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
                run.flow.clear_participation_stats()
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

                # category_counts = self.object.get_ruleset_category_counts()
                runs = FlowRun.objects.filter(flow=self.object).exclude(contact__is_test=True)

                if 'sSearch' in self.request.REQUEST:
                    query = self.request.REQUEST['sSearch']
                    if org.is_anon:
                        # try casting our query to an int if they are querying by contact id
                        query_int = -1
                        try:
                            query_int = int(query)
                        except:
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

                runs = runs[start:(start+show)]

                # fetch the step data for our set of contacts
                contacts = []
                for run in runs:
                    contacts.append(run['contact__pk'])

                steps = FlowStep.objects.filter(run__flow=self.object, run__contact__in=contacts).exclude(rule_value=None).order_by('run__contact__pk', 'step_uuid', '-arrived_on').distinct('run__contact__pk', 'step_uuid')

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
                                 dict(contact=run['contact__pk'], category=run['count'])]
                                + cols)

                return build_json_response(dict(iTotalRecords=total, iTotalDisplayRecords=total, sEcho=self.request.REQUEST.get('sEcho', 0), aaData=rows))
            else:
                if 'c' in self.request.REQUEST:
                    contact = Contact.objects.filter(pk=self.request.REQUEST['c'], org=self.object.org, is_active=True)
                    if contact:
                        contact = contact[0]
                        runs = list(contact.runs.filter(flow=self.object).order_by('-created_on'))
                        for run in runs:
                            # step_uuid__in=step_uuids
                            run.__dict__['messages'] = list(Msg.objects.filter(steps__run=run).order_by('created_on'))
                        context['runs'] = runs
                        context['contact'] = contact

                else:
                    context['counts'] = self.object.get_ruleset_category_counts()

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

            steps_json = {}
            visited_json = {}

            sim = request.REQUEST.get('simulation', 'false') == 'true'

            cached_activity = cache.get('activity:%d' % flow.pk, None)
            if cached_activity is None:
                start = time.time()

                active = FlowStep.objects.filter(run__is_active=True, run__flow=flow, left_on=None, run__contact__is_test=sim).values('step_uuid').annotate(count=Count('run'))
                visited_actions = FlowStep.objects.filter(run__flow=flow, step_type='A', run__contact__is_test=sim).values('step_uuid', 'next_uuid').annotate(count=Count('run'))
                visited_rules = FlowStep.objects.filter(run__flow=flow, step_type='R', run__contact__is_test=sim).exclude(rule_uuid=None).values('rule_uuid', 'next_uuid').annotate(count=Count('run'))

                for step in active:
                    steps_json[step['step_uuid']] = dict(count=step['count'])

                for step in visited_actions:
                    visited_json['%s->%s' % (step['step_uuid'], step['next_uuid'])] = dict(count=step['count'])

                for path in visited_rules:
                    visited_json['%s->%s' % (path['rule_uuid'], path['next_uuid'])] = dict(count=path['count'])

                cached_activity = dict(visited_actions=visited_actions, visited_rules=visited_rules,
                                       steps_json=steps_json, visited_json=visited_json)

                # our cache time is our time to calculate times two or 5 seconds, whichever is larger
                cache_time = max(5, (time.time() - start) * 2)
                cache.set('activity:%d' % flow.pk, cached_activity, cache_time)

            # if we are interested in the flow details add that
            flow_json = dict()
            if request.REQUEST.get('flow', 0):
                flow_json = flow.as_json()

            # get our latest start, we might warn the user that one is in progress
            start = flow.starts.all().order_by('-created_on')
            pending = None
            if start.count() and (start[0].status == STARTING or start[0].status == PENDING):
                pending = start[0].status

            # if we have an active call, include that
            from temba.ivr.models import IVRCall
            call_id = request.REQUEST.get('call', None)

            test_call = IVRCall.objects.filter(contact__is_test=True, flow=flow)
            if test_call:
                test_call = test_call[0]
                if sim:
                    call_id = test_call.pk

            call = dict()

            messages = []
            if call_id:
                calls = IVRCall.objects.filter(pk=call_id, org=flow.org)
                if calls:
                    call = calls[0]
                    call = dict(pk=call.pk,
                                call_type=call.call_type,
                                status=call.status,
                                duration=call.get_duration(),
                                number=call.contact.raw_tel())
                    if sim:
                        messages = Msg.objects.filter(contact=Contact.get_test_contact(self.request.user)).order_by('created_on')
                        action_logs = list(ActionLog.objects.filter(run__flow=flow, run__contact__is_test=True).order_by('created_on'))

                        messages_and_logs = chain(messages, action_logs)
                        messages_and_logs = sorted(messages_and_logs, cmp=msg_log_cmp)

                        messages_json = []
                        if messages_and_logs:
                            for msg in messages_and_logs:
                                messages_json.append(msg.as_json())
                        messages = messages_json

            return build_json_response(dict(call=call, messages=messages,
                                            activity=cached_activity['steps_json'],
                                            visited=cached_activity['visited_json'],
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
                Msg.objects.filter(contact=test_contact).delete()
                IVRCall.objects.filter(contact=test_contact).delete()

                runs.delete()
                steps.delete()

                # reset the name for our test contact too
                test_contact.name = "%s %s" % (request.user.first_name, request.user.last_name)
                test_contact.save()

                flow.start([], [test_contact], restart_participants=True)

            # try to create message
            if 'new_message' in json_dict:
                try:
                    Msg.create_incoming(None,
                                        (TEL_SCHEME, test_contact.get_urn(TEL_SCHEME).path),
                                        json_dict['new_message'],
                                        org=user.get_org())
                except Exception as e:
                    import traceback; traceback.print_exc(e)
                    return build_json_response(dict(status="error", description="Error creating message: %s" % str(e)), status=400)

            messages = Msg.objects.filter(contact=test_contact).order_by('pk', 'created_on')
            action_logs = ActionLog.objects.filter(run__contact=test_contact).order_by('pk', 'created_on')

            messages_and_logs = chain(messages, action_logs)
            messages_and_logs = sorted(messages_and_logs, cmp=msg_log_cmp)

            messages_json = []
            if messages_and_logs:
                for msg in messages_and_logs:
                    messages_json.append(msg.as_json())

            steps_json = {}
            visited_json = {}

            active = FlowStep.objects.filter(run__flow=flow, run__contact=test_contact, left_on=None).values('step_uuid').annotate(count=Count('run'))
            visited_actions = FlowStep.objects.filter(run__flow=flow, run__contact=test_contact, step_type='A').values('step_uuid', 'next_uuid').annotate(count=Count('run'))
            visited_rules = FlowStep.objects.filter(run__flow=flow, run__contact=test_contact, step_type='R').exclude(rule_uuid=None).values('rule_uuid', 'next_uuid').annotate(count=Count('run'))

            for step in active:
                steps_json[step['step_uuid']] = dict(count=step['count'])

            for step in visited_actions:
                visited_json['%s->%s' % (step['step_uuid'], step['next_uuid'])] = dict(count=step['count'])

            for path in visited_rules:
                visited_json['%s->%s' % (path['rule_uuid'], path['next_uuid'])] = dict(count=path['count'])

            return build_json_response(dict(status="success", description="Message sent to Flow", messages=messages_json, activity=steps_json, visited=visited_json))

    class Json(OrgObjPermsMixin, SmartUpdateView):
        success_message = ''

        def get(self, request, *args, **kwargs):

            flow = self.get_object()

            # all the translation languages for our org
            languages = [lang.as_json() for lang in flow.org.languages.all().order_by('orgs')]
            return build_json_response(dict(flow=flow.as_json(expand_contacts=True), languages=languages))

        def post(self, request, *args, **kwargs):
            # try to parse our body
            json_string = request.body

            analytics.track(self.request.user.username, 'temba.flow_updated')

            # try to save the our flow, if this fails, let's let that bubble up to our logger
            json_dict = json.loads(json_string)
            print json.dumps(json_dict, indent=2)
            response_data = self.get_object(self.get_queryset()).update(json_dict, user=self.request.user)
            return build_json_response(response_data, status=200)

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
                if FlowStart.objects.filter(flow=self.flow).exclude(status__in=[COMPLETE, FAILED]):
                    raise ValidationError(_("This flow is already being started, please wait until that process is complete before starting more contacts."))

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
            context['participant_count'] = self.object.participant_count()
            context['complete_count'] = self.object.completed_count()
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
                flow_ids = [ int(_) for _ in self.form.cleaned_data['flows'].split(',') if _.isdigit() ]

            flows = Flow.objects.filter(org=obj.org, pk__in=flow_ids)

            if flows:
                obj.toggle_label(flows, add=True)

            return obj
