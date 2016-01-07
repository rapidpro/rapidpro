from uuid import uuid4
from django.core.exceptions import ValidationError
from django.forms import ModelForm
from django.views.decorators.csrf import csrf_exempt
from django.contrib.auth.models import Group
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.contacts.models import ContactGroup, ContactField
from temba.msgs.views import BaseActionForm
from temba.flows.models import Flow
from smartmin.views import *

from .models import Campaign, CampaignEvent, EventFire, UNIT_CHOICES, HOURS


class CampaignActionForm(BaseActionForm):
    ALLOWED_ACTIONS = (('archive', "Archive Campaigns"),
                       ('restore', "Restore Campaigns"))

    OBJECT_CLASS = Campaign
    HAS_IS_ACTIVE = True

    class Meta:
        fields = ('action', 'objects')


class CampaignActionMixin(SmartListView):

    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super(CampaignActionMixin, self).dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        user = self.request.user
        form = CampaignActionForm(self.request.POST, org=user.get_org(), user=user)

        if form.is_valid():
            form.execute()

        return self.get(request, *args, **kwargs)


class UpdateCampaignForm(ModelForm):
    group = forms.ModelChoiceField(queryset=ContactGroup.user_groups.filter(pk__lt=0),
                                   required=True, label="Group",
                                   help_text="Which group this campaign operates on")

    def __init__(self, *args, **kwargs):
        self.user = kwargs['user']
        del kwargs['user']

        super(UpdateCampaignForm, self).__init__(*args, **kwargs)
        self.fields['group'].initial = self.instance.group
        self.fields['group'].queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True)

    class Meta:
        model = Campaign
        fields = '__all__'


class CampaignCRUDL(SmartCRUDL):
    model = Campaign
    actions = ('create', 'read', 'update', 'list', 'archived')

    class OrgMixin(OrgPermsMixin):
        def derive_queryset(self, *args, **kwargs):
            queryset = super(CampaignCRUDL.OrgMixin, self).derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated():
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Update(OrgMixin, ModalMixin, SmartUpdateView):
        fields = ('name', 'group')
        success_message = ''
        form_class = UpdateCampaignForm

        def get_success_url(self):
            return reverse('campaigns.campaign_read', args=[self.object.pk])

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super(CampaignCRUDL.Update, self).get_form_kwargs(*args, **kwargs)
            form_kwargs['user'] = self.request.user
            return form_kwargs

        def form_valid(self, form):
            previous_group = self.get_object().group
            new_group = form.cleaned_data['group']

            group_changed = new_group != previous_group
            if group_changed:
                fires = EventFire.objects.filter(event__campaign=self.object, event__campaign__group=previous_group, fired=None)
                fires.delete()

            # save our campaign
            self.object = form.save(commit=False)
            self.save(self.object)

            # if our group changed, create our new fires
            if group_changed:
                EventFire.update_campaign_events(self.object)

            response = self.render_to_response(self.get_context_data(form=form,
                                                                     success_url=self.get_success_url(),
                                                                     success_script=getattr(self, 'success_script', None)))
            response['Temba-Success'] = self.get_success_url()
            return response

    class Read(OrgMixin, SmartReadView):
        def get_gear_links(self):
            links = []
            if self.has_org_perm("campaigns.campaignevent_create"):
                links.append(dict(title='Add Event',
                                  style='btn-primary',
                                  js_class='add-event',
                                  href='#'))

            if self.has_org_perm("campaigns.campaign_update"):
                links.append(dict(title='Edit',
                                  js_class='update-campaign',
                                  href='#'))

            return links

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):
        class CampaignForm(forms.ModelForm):
            def __init__(self, user, *args, **kwargs):
                self.user = user
                super(CampaignCRUDL.Create.CampaignForm, self).__init__(*args, **kwargs)

                group = self.fields['group']
                group.queryset = ContactGroup.user_groups.filter(org=self.user.get_org(), is_active=True).order_by('name')
                group.user = user

            class Meta:
                model = Campaign
                fields = '__all__'

        fields = ('name', 'group')
        form_class = CampaignForm
        success_message = ""
        success_url = 'id@campaigns.campaign_read'

        def pre_save(self, obj):
            obj = super(CampaignCRUDL.Create, self).pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def get_form_kwargs(self):
            kwargs = super(CampaignCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

    class BaseList(CampaignActionMixin, OrgMixin, OrgPermsMixin, SmartListView):
        fields = ('name', 'group',)
        default_template = 'campaigns/campaign_list.html'
        default_order = ('-modified_on',)

        def get_context_data(self, **kwargs):
            context = super(CampaignCRUDL.BaseList, self).get_context_data(**kwargs)
            context['org_has_campaigns'] = Campaign.objects.filter(org=self.request.user.get_org()).count()
            context['folders']= self.get_folders()
            context['request_url'] = self.request.path
            context['actions'] = self.actions
            return context

        def get_folders(self):
            org = self.request.user.get_org()
            folders = []
            folders.append(dict(label="Active", url=reverse('campaigns.campaign_list'), count=Campaign.objects.filter(is_active=True, is_archived=False, org=org).count()))
            folders.append(dict(label="Archived", url=reverse('campaigns.campaign_archived'), count=Campaign.objects.filter(is_active=True, is_archived=True, org=org).count()))
            return folders

    class List(BaseList):
        fields = ('name', 'group',)
        actions = ('archive',)
        search_fields = ('name__icontains', 'group__name__icontains')

        def get_queryset(self, *args, **kwargs):
            qs = super(CampaignCRUDL.List, self).get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=False)
            return qs

    class Archived(BaseList):
        fields = ('name',)
        actions = ('restore',)

        def get_queryset(self, *args, **kwargs):
            qs = super(CampaignCRUDL.Archived, self).get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=True)
            return qs


class EventForm(forms.ModelForm):

    event_type = forms.ChoiceField(choices=(('M', "Send a message"),
                                            ('F', "Start a flow")), required=True)

    message = forms.CharField(widget=forms.Textarea, required=False)

    direction = forms.ChoiceField(choices=(('B', "Before"),
                                           ('A', "After")), required=True)

    unit = forms.ChoiceField(choices=UNIT_CHOICES, required=True)

    flow_to_start = forms.ModelChoiceField(queryset=Flow.objects.filter(is_active=True), required=False)

    delivery_hour = forms.ChoiceField(choices=CampaignEvent.get_hour_choices(), required=False)

    def clean(self):
        return super(EventForm, self).clean()

    def clean_message(self):
        if self.data['event_type'] == 'M':
            if 'message' not in self.data or not self.data['message'].strip():
                raise ValidationError("Please enter a message")
        return self.data['message']

    def clean_flow_to_start(self):
        if self.data['event_type'] == 'F':
            if 'flow_to_start' not in self.data or not self.data['flow_to_start']:
                raise ValidationError("Please select a flow")
        return self.data['flow_to_start']

    def pre_save(self, request, obj):
        # if it's before, negate the offset
        if self.cleaned_data['direction'] == 'B':
            obj.offset = -obj.offset

        if self.cleaned_data['unit'] == 'H' or self.cleaned_data['unit'] == 'M':
            obj.delivery_hour = -1

        # if its a message flow, set that accordingly
        if self.cleaned_data['event_type'] == 'M':

            if not obj.flow_id or not obj.flow.is_active or obj.flow.flow_type != Flow.MESSAGE:
                obj.flow = Flow.create_single_message(request.user.get_org(), request.user,
                                                      self.cleaned_data['message'])

            # set our single message on our flow
            obj.flow.update_single_message_flow(message=self.cleaned_data['message'])
            obj.message = self.cleaned_data['message']

        # otherwise, it's an event that runs an existing flow
        else:
            obj.flow = Flow.objects.get(pk=self.cleaned_data['flow_to_start'])

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(EventForm, self).__init__(*args, **kwargs)

        relative_to = self.fields['relative_to']
        relative_to.queryset = ContactField.objects.filter(org=self.user.get_org(), is_active=True).order_by('label')

        flow = self.fields['flow_to_start']
        flow.queryset = Flow.objects.filter(org=self.user.get_org(), flow_type__in=[Flow.FLOW, Flow.VOICE], is_active=True, is_archived=False).order_by('name')

    class Meta:
        model = CampaignEvent
        fields = '__all__'


class CampaignEventCRUDL(SmartCRUDL):
    model = CampaignEvent
    actions = ('create', 'delete', 'read', 'update')

    class Read(OrgObjPermsMixin, SmartReadView):

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            context = super(CampaignEventCRUDL.Read, self).get_context_data(**kwargs)
            event_fires = self.get_object().event_fires.all()

            fired_event_fires = event_fires.exclude(fired=None).order_by('fired', 'pk')
            scheduled_event_fires = event_fires.filter(fired=None).order_by('scheduled', 'pk')

            fired = fired_event_fires[:25]
            context['fired_event_fires'] = fired
            context['fired_event_fires_count'] = fired_event_fires.count() - len(fired)

            scheduled = scheduled_event_fires[:25]
            context['scheduled_event_fires'] = scheduled
            context['scheduled_event_fires_count'] = scheduled_event_fires.count() - len(scheduled)

            return context

        def get_gear_links(self):
            links = []
            if self.has_org_perm("campaigns.campaignevent_update"):
                links.append(dict(title='Edit',
                                  js_class='update-event',
                                  href='#'))

            if self.has_org_perm("campaigns.campaignevent_delete"):
                links.append(dict(title='Delete',
                                  delete=True,
                                  success_url=reverse('campaigns.campaign_read', args=[self.get_object().campaign.pk]),
                                  href=reverse('campaigns.campaignevent_delete', args=[self.get_object().id])))

            return links

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):

        default_template = 'smartmin/delete_confirm.html'

        def get_object_org(self):
            return self.get_object().campaign.org

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.is_active = False
            self.object.save()

            EventFire.update_eventfires_for_event(self.object)

            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self):
            return reverse('campaigns.campaign_read', args=[self.object.campaign.pk])

        def get_cancel_url(self):
            return reverse('campaigns.campaign_read', args=[self.object.campaign.pk])

    class Update(OrgPermsMixin, ModalMixin, SmartUpdateView):
        success_message = ''
        form_class = EventForm

        fields = ('event_type', 'message', 'flow_to_start', 'offset', 'unit', 'direction', 'relative_to', 'delivery_hour')

        def get_form_kwargs(self):
            kwargs = super(CampaignEventCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super(CampaignEventCRUDL.Update, self).derive_initial()

            if self.object.offset < 0:
                initial['direction'] = 'B'
                initial['offset'] = abs(self.object.offset)
            else:
                initial['direction'] = 'A'

            if self.object.event_type == 'F':
                initial['flow_to_start'] = self.object.flow

            return initial

        def post_save(self, obj):
            obj = super(CampaignEventCRUDL.Update, self).post_save(obj)
            obj.update_flow_name()
            EventFire.update_eventfires_for_event(obj)
            return obj

        def pre_save(self, obj):

            prev = CampaignEvent.objects.get(pk=obj.pk)
            if prev.event_type == 'M' and obj.event_type == 'F' and prev.flow:
                flow = prev.flow
                flow.is_active = False
                flow.save()
                obj.message = None

            obj = super(CampaignEventCRUDL.Update, self).pre_save(obj)
            self.form.pre_save(self.request, obj)
            return obj

        def get_success_url(self):
            return reverse('campaigns.campaignevent_read', args=[self.object.pk])

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):

        fields = ('event_type', 'message', 'flow_to_start', 'offset', 'unit', 'direction', 'relative_to', 'delivery_hour')
        form_class = EventForm
        success_message = ""
        template_name = "campaigns/campaignevent_update.haml"

        def get_success_url(self):
            return reverse('campaigns.campaign_read', args=[self.object.campaign.pk])

        def get_form_kwargs(self):
            kwargs = super(CampaignEventCRUDL.Create, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super(CampaignEventCRUDL.Create, self).derive_initial()
            initial['unit'] = 'D'
            initial['offset'] = '15'
            initial['direction'] = 'A'
            return initial

        def post_save(self, obj):
            obj = super(CampaignEventCRUDL.Create, self).post_save(obj)
            obj.update_flow_name()
            EventFire.update_eventfires_for_event(obj)
            return obj

        def pre_save(self, obj):
            obj = super(CampaignEventCRUDL.Create, self).pre_save(obj)
            obj.campaign = Campaign.objects.get(org=self.request.user.get_org(), pk=self.request.REQUEST.get('campaign'))
            self.form.pre_save(self.request, obj)
            return obj
