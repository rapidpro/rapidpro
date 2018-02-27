# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django import forms
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from django.utils.translation import ugettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from smartmin.views import SmartCRUDL, SmartListView, SmartUpdateView, SmartCreateView, SmartReadView, SmartDeleteView
from temba.contacts.models import ContactGroup, ContactField
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.views import OrgPermsMixin, OrgObjPermsMixin, ModalMixin
from temba.utils.views import BaseActionForm

from .models import Campaign, CampaignEvent, EventFire


class CampaignActionForm(BaseActionForm):
    allowed_actions = (('archive', "Archive Campaigns"),
                       ('restore', "Restore Campaigns"))

    model = Campaign
    has_is_active = True

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


class UpdateCampaignForm(forms.ModelForm):
    group = forms.ModelChoiceField(queryset=ContactGroup.user_groups.none(),
                                   required=True, label="Group",
                                   help_text="Which group this campaign operates on")

    def __init__(self, *args, **kwargs):
        self.user = kwargs['user']
        del kwargs['user']

        super(UpdateCampaignForm, self).__init__(*args, **kwargs)
        self.fields['group'].initial = self.instance.group
        self.fields['group'].queryset = ContactGroup.get_user_groups(self.user.get_org(), ready_only=False)

    class Meta:
        model = Campaign
        fields = '__all__'


class CampaignCRUDL(SmartCRUDL):
    model = Campaign
    actions = ('create', 'read', 'update', 'list', 'archived')

    class OrgMixin(OrgPermsMixin):
        def derive_queryset(self, *args, **kwargs):
            queryset = super(CampaignCRUDL.OrgMixin, self).derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated():  # pragma: no cover
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
                super(CampaignCRUDL.Create.CampaignForm, self).__init__(*args, **kwargs)

                self.fields['group'].queryset = ContactGroup.get_user_groups(user.get_org()).order_by('name')

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
            context['folders'] = self.get_folders()
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

    event_type = forms.ChoiceField(choices=((CampaignEvent.TYPE_MESSAGE, "Send a message"),
                                            (CampaignEvent.TYPE_FLOW, "Start a flow")), required=True)

    direction = forms.ChoiceField(choices=(('B', "Before"),
                                           ('A', "After")), required=True)

    unit = forms.ChoiceField(choices=CampaignEvent.UNIT_CHOICES, required=True)

    flow_to_start = forms.ModelChoiceField(queryset=Flow.objects.filter(is_active=True), required=False)

    delivery_hour = forms.ChoiceField(choices=CampaignEvent.get_hour_choices(), required=False)

    def clean(self):
        data = super(EventForm, self).clean()
        if self.data['event_type'] == CampaignEvent.TYPE_MESSAGE and self.languages:
            language = self.languages[0].language
            iso_code = language['iso_code']
            if iso_code not in self.data or not self.data[iso_code].strip():
                raise ValidationError(_("A message is required for '%s'") % language['name'])

            for lang_data in self.languages:
                lang = lang_data.language
                iso_code = lang['iso_code']
                if iso_code in self.data and len(self.data[iso_code].strip()) > Msg.MAX_TEXT_LEN:
                    raise ValidationError(
                        _("Translation for '%s' exceeds the %d character limit.") % (lang['name'], Msg.MAX_TEXT_LEN))

        return data

    def clean_flow_to_start(self):
        if self.data['event_type'] == CampaignEvent.TYPE_FLOW:
            if 'flow_to_start' not in self.data or not self.data['flow_to_start']:
                raise ValidationError("Please select a flow")
        return self.data['flow_to_start']

    def pre_save(self, request, obj):
        org = self.user.get_org()

        # if it's before, negate the offset
        if self.cleaned_data['direction'] == 'B':
            obj.offset = -obj.offset

        if self.cleaned_data['unit'] == 'H' or self.cleaned_data['unit'] == 'M':  # pragma: needs cover
            obj.delivery_hour = -1

        # if its a message flow, set that accordingly
        if self.cleaned_data['event_type'] == CampaignEvent.TYPE_MESSAGE:

            if self.instance.id:
                base_language = self.instance.flow.base_language
            else:
                base_language = org.primary_language.iso_code if org.primary_language else 'base'

            translations = {}
            for language in self.languages:
                iso_code = language.language['iso_code']
                translations[iso_code] = self.cleaned_data.get(iso_code, '')

            if not obj.flow_id or not obj.flow.is_active or obj.flow.flow_type != Flow.MESSAGE:
                obj.flow = Flow.create_single_message(org, request.user, translations, base_language=base_language)
            else:
                # set our single message on our flow
                obj.flow.update_single_message_flow(translations, base_language)

            obj.message = translations
            obj.full_clean()

        # otherwise, it's an event that runs an existing flow
        else:
            obj.flow = Flow.objects.get(org=org, id=self.cleaned_data['flow_to_start'])

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super(EventForm, self).__init__(*args, **kwargs)

        org = self.user.get_org()

        relative_to = self.fields['relative_to']
        relative_to.queryset = ContactField.objects.filter(org=org, is_active=True).order_by('label')

        flow = self.fields['flow_to_start']
        flow.queryset = Flow.objects.filter(org=self.user.get_org(), flow_type__in=[Flow.FLOW, Flow.VOICE],
                                            is_active=True, is_archived=False).order_by('name')

        message = self.instance.message or {}
        self.languages = []

        # add in all of our languages for message forms
        languages = org.languages.all()

        for language in languages:

            insert = None

            # if it's our primary language, allow use to steal the 'base' message
            if org.primary_language and org.primary_language.iso_code == language.iso_code:

                initial = message.get(language.iso_code)

                if not initial:
                    initial = message.get('base')

                # also, let's show it first
                insert = 0
            else:

                # otherwise, its just a normal language
                initial = message.get(language.iso_code)

            field = forms.CharField(widget=forms.Textarea, required=False, label=language.name, initial=initial)
            self.fields[language.iso_code] = field
            field.language = dict(name=language.name, iso_code=language.iso_code)

            # see if we need to insert or append
            if insert is not None:
                self.languages.insert(insert, field)
            else:
                self.languages.append(field)

        # determine our base language if necessary
        base_language = None
        if not org.primary_language:
            base_language = 'base'

        # if we are editing, always include the flow base language
        if self.instance.id:
            base_language = self.instance.flow.base_language

        # add our default language, we'll insert it at the front of the list
        if base_language and base_language not in self.fields:
            field = forms.CharField(widget=forms.Textarea, required=False,
                                    label=_('Default'),
                                    initial=message.get(base_language))

            self.fields[base_language] = field
            field.language = dict(iso_code=base_language, name='Default')
            self.languages.insert(0, field)

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
            self.object.release()

            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self):
            return reverse('campaigns.campaign_read', args=[self.object.campaign.pk])

        def get_cancel_url(self):  # pragma: needs cover
            return reverse('campaigns.campaign_read', args=[self.object.campaign.pk])

    class Update(OrgPermsMixin, ModalMixin, SmartUpdateView):
        success_message = ''
        form_class = EventForm

        default_fields = ['event_type', 'flow_to_start', 'offset', 'unit', 'direction', 'relative_to', 'delivery_hour']

        def get_form_kwargs(self):
            kwargs = super(CampaignEventCRUDL.Update, self).get_form_kwargs()
            kwargs['user'] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            return super(CampaignEventCRUDL.Update, self).get_context_data(**kwargs)

        def derive_fields(self):

            from copy import deepcopy
            fields = deepcopy(self.default_fields)

            # add in all of our languages for message forms
            org = self.request.user.get_org()

            for language in org.languages.all():
                fields.append(language.iso_code)

            flow_language = self.object.flow.base_language

            if flow_language not in fields:
                fields.append(flow_language)

            return fields

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
            if prev.event_type == 'M' and obj.event_type == 'F' and prev.flow:  # pragma: needs cover
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

        default_fields = ['event_type', 'flow_to_start', 'offset', 'unit', 'direction', 'relative_to', 'delivery_hour']
        form_class = EventForm
        success_message = ""
        template_name = "campaigns/campaignevent_update.haml"

        def derive_fields(self):

            from copy import deepcopy
            fields = deepcopy(self.default_fields)

            # add in all of our languages for message forms
            org = self.request.user.get_org()

            for language in org.languages.all():
                fields.append(language.iso_code)

            if not org.primary_language:
                fields.append('base')

            return fields

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
            obj.campaign = Campaign.objects.get(org=self.request.user.get_org(), pk=self.request.GET.get('campaign'))
            self.form.pre_save(self.request, obj)
            return obj
