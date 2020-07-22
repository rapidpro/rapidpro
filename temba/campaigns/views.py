from smartmin.views import SmartCreateView, SmartCRUDL, SmartDeleteView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.contacts.models import ContactField, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.fields import CompletionTextarea, InputWidget, SelectWidget
from temba.utils.views import BulkActionMixin
from temba.values.constants import Value

from .models import Campaign, CampaignEvent, EventFire


class UpdateCampaignForm(forms.ModelForm):
    group = forms.ModelChoiceField(
        queryset=ContactGroup.user_groups.none(),
        empty_label=None,
        widget=SelectWidget(attrs={"placeholder": _("Select a group to base the campaign on"), "searchable": True}),
    )

    def __init__(self, *args, **kwargs):
        self.user = kwargs["user"]
        del kwargs["user"]

        super().__init__(*args, **kwargs)
        self.fields["group"].initial = self.instance.group
        self.fields["group"].queryset = ContactGroup.get_user_groups(self.user.get_org(), ready_only=False)

    class Meta:
        model = Campaign
        fields = ("name", "group")

        widgets = {"name": InputWidget()}


class CampaignCRUDL(SmartCRUDL):
    model = Campaign
    actions = ("create", "read", "update", "list", "archived", "archive", "activate")

    class OrgMixin(OrgPermsMixin):
        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            if not self.request.user.is_authenticated:  # pragma: no cover
                return queryset.exclude(pk__gt=0)
            else:
                return queryset.filter(org=self.request.user.get_org())

    class Update(OrgMixin, ModalMixin, SmartUpdateView):
        fields = ("name", "group")
        success_message = ""
        form_class = UpdateCampaignForm

        def pre_process(self, request, *args, **kwargs):
            campaign_id = kwargs.get("pk")
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.pk])

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["user"] = self.request.user
            return form_kwargs

        def form_valid(self, form):
            previous_group = self.get_object().group
            new_group = form.cleaned_data["group"]

            # save our campaign
            self.object = form.save(commit=False)
            self.save(self.object)

            # if our group changed, create our new fires
            if new_group != previous_group:
                EventFire.update_campaign_events(self.object)

            response = self.render_to_response(
                self.get_context_data(
                    form=form, success_url=self.get_success_url(), success_script=getattr(self, "success_script", None)
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response

    class Read(OrgObjPermsMixin, SmartReadView):
        def get_gear_links(self):
            links = []

            if self.object.is_archived:
                if self.has_org_perm("campaigns.campaign_activate"):
                    links.append(
                        dict(
                            title="Activate",
                            js_class="posterize activate-campaign",
                            href=reverse("campaigns.campaign_activate", args=[self.object.id]),
                        )
                    )

                if self.has_org_perm("orgs.org_export"):
                    links.append(
                        dict(
                            title=_("Export"),
                            href=f"{reverse('orgs.org_export')}?campaign={self.object.id}&archived=1",
                        )
                    )

            else:
                if self.has_org_perm("campaigns.campaignevent_create"):
                    links.append(
                        dict(
                            id="event-add",
                            title=_("Add Event"),
                            href=f"{reverse('campaigns.campaignevent_create')}?campaign={self.object.pk}",
                            modax=_("Add Event"),
                        )
                    )
                if self.has_org_perm("orgs.org_export"):
                    links.append(
                        dict(title=_("Export"), href=f"{reverse('orgs.org_export')}?campaign={self.object.id}")
                    )

                if self.has_org_perm("campaigns.campaign_update"):
                    links.append(
                        dict(
                            id="campaign-update",
                            title=_("Edit"),
                            href=reverse("campaigns.campaign_update", args=[self.object.pk]),
                            modax=_("Update Campaign"),
                        )
                    )

                if self.has_org_perm("campaigns.campaign_archive"):
                    links.append(
                        dict(
                            title="Archive",
                            js_class="posterize archive-campaign",
                            href=reverse("campaigns.campaign_archive", args=[self.object.id]),
                        )
                    )

            user = self.get_user()
            if user.is_superuser or user.is_staff:
                links.append(
                    dict(
                        title=_("Service"),
                        posterize=True,
                        href=f'{reverse("orgs.org_service")}?organization={self.object.org_id}&redirect_url={reverse("campaigns.campaign_read", args=[self.object.id])}',
                    )
                )

            return links

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):
        class CampaignForm(forms.ModelForm):

            group = forms.ModelChoiceField(
                queryset=ContactGroup.user_groups.none(),
                required=True,
                empty_label=None,
                widget=SelectWidget(
                    attrs={"placeholder": _("Select a group to base the campaign on"), "searchable": True}
                ),
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.fields["group"].queryset = ContactGroup.get_user_groups(user.get_org()).order_by("name")

            class Meta:
                model = Campaign
                fields = ("name", "group")

                widgets = {"name": InputWidget()}

        fields = ("name", "group")
        form_class = CampaignForm
        success_message = ""
        success_url = "id@campaigns.campaign_read"

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.user.get_org()
            return obj

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

    class BaseList(OrgMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        fields = ("name", "group")
        default_template = "campaigns/campaign_list.html"
        default_order = ("-modified_on",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_campaigns"] = Campaign.objects.filter(org=self.request.user.get_org()).count()
            context["folders"] = self.get_folders()
            context["request_url"] = self.request.path
            return context

        def get_folders(self):
            org = self.request.user.get_org()
            folders = []
            folders.append(
                dict(
                    label="Active",
                    url=reverse("campaigns.campaign_list"),
                    count=Campaign.objects.filter(is_active=True, is_archived=False, org=org).count(),
                )
            )
            folders.append(
                dict(
                    label="Archived",
                    url=reverse("campaigns.campaign_archived"),
                    count=Campaign.objects.filter(is_active=True, is_archived=True, org=org).count(),
                )
            )
            return folders

    class List(BaseList):
        fields = ("name", "group")
        bulk_actions = ("archive",)
        search_fields = ("name__icontains", "group__name__icontains")

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=False)
            return qs

    class Archived(BaseList):
        fields = ("name",)
        bulk_actions = ("restore",)

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=True)
            return qs

    class Archive(OrgMixin, OrgPermsMixin, SmartUpdateView):

        fields = ()
        success_url = "id@campaigns.campaign_read"
        success_message = _("Campaign archived")

        def save(self, obj):
            obj.apply_action_archive(self.request.user, Campaign.objects.filter(id=obj.id))
            return obj

    class Activate(OrgMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "id@campaigns.campaign_read"
        success_message = _("Campaign activated")

        def save(self, obj):
            obj.apply_action_restore(self.request.user, Campaign.objects.filter(id=obj.id))
            return obj


class CampaignEventForm(forms.ModelForm):

    event_type = forms.ChoiceField(
        choices=((CampaignEvent.TYPE_MESSAGE, "Send a message"), (CampaignEvent.TYPE_FLOW, "Start a flow")),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select the event type"), "widget_only": True}),
    )

    direction = forms.ChoiceField(
        choices=(("B", "Before"), ("A", "After")),
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Relative date direction"), "widget_only": True}),
    )

    unit = forms.ChoiceField(
        choices=CampaignEvent.UNIT_CHOICES,
        required=True,
        widget=SelectWidget(attrs={"placeholder": _("Select a unit"), "widget_only": True}),
    )

    flow_to_start = forms.ModelChoiceField(
        queryset=Flow.objects.filter(is_active=True),
        required=False,
        empty_label=None,
        widget=SelectWidget(attrs={"placeholder": _("Select a flow to start"), "widget_only": True}),
    )

    relative_to = forms.ModelChoiceField(
        queryset=ContactField.all_fields.none(),
        required=False,
        empty_label=None,
        widget=SelectWidget(
            attrs={
                "placeholder": _("Select a date field to base this event on"),
                "widget_only": True,
                "searchable": True,
            }
        ),
    )

    delivery_hour = forms.ChoiceField(
        choices=CampaignEvent.get_hour_choices(),
        required=False,
        widget=SelectWidget(attrs={"placeholder": _("Select hour for delivery"), "widget_only": True}),
    )

    flow_start_mode = forms.ChoiceField(
        choices=(
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and start this event")),
            (CampaignEvent.MODE_SKIP, _("Skip this event")),
        ),
        required=False,
        widget=SelectWidget(attrs={"placeholder": _("Flow starting rules"), "widget_only": True}),
    )

    message_start_mode = forms.ChoiceField(
        choices=(
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and send the message")),
            (CampaignEvent.MODE_SKIP, _("Skip this message")),
        ),
        required=False,
        widget=SelectWidget(attrs={"placeholder": _("Message sending rules"), "widget_only": True}),
    )

    def clean(self):
        data = super().clean()
        if self.data["event_type"] == CampaignEvent.TYPE_MESSAGE and self.languages:
            language = self.languages[0].language
            iso_code = language["iso_code"]
            if iso_code not in self.data or not self.data[iso_code].strip():
                raise ValidationError(_("A message is required for '%s'") % language["name"])

            for lang_data in self.languages:
                lang = lang_data.language
                iso_code = lang["iso_code"]
                if iso_code in self.data and len(self.data[iso_code].strip()) > Msg.MAX_TEXT_LEN:
                    raise ValidationError(
                        _("Translation for '%(language)s' exceeds the %(limit)d character limit.")
                        % dict(language=lang["name"], limit=Msg.MAX_TEXT_LEN)
                    )

        return data

    def clean_flow_to_start(self):
        if self.data["event_type"] == CampaignEvent.TYPE_FLOW:
            if "flow_to_start" not in self.data or not self.data["flow_to_start"]:
                raise ValidationError("Please select a flow")
        return self.data["flow_to_start"]

    def pre_save(self, request, obj):
        org = self.user.get_org()

        # if it's before, negate the offset
        if self.cleaned_data["direction"] == "B":
            obj.offset = -obj.offset

        if self.cleaned_data["unit"] == "H" or self.cleaned_data["unit"] == "M":  # pragma: needs cover
            obj.delivery_hour = -1

        # if its a message flow, set that accordingly
        if self.cleaned_data["event_type"] == CampaignEvent.TYPE_MESSAGE:

            if self.instance.id:
                base_language = self.instance.flow.base_language
            else:
                base_language = org.primary_language.iso_code if org.primary_language else "base"

            translations = {}
            for language in self.languages:
                iso_code = language.language["iso_code"]
                if iso_code in self.cleaned_data and self.cleaned_data.get(iso_code, "").strip():
                    translations[iso_code] = self.cleaned_data.get(iso_code, "").strip()

            if not obj.flow_id or not obj.flow.is_active or not obj.flow.is_system:
                obj.flow = Flow.create_single_message(org, request.user, translations, base_language=base_language)
            else:
                # set our single message on our flow
                obj.flow.update_single_message_flow(self.user, translations, base_language)

            obj.message = translations
            obj.full_clean()
            obj.start_mode = self.cleaned_data["message_start_mode"]

        # otherwise, it's an event that runs an existing flow
        else:
            obj.flow = Flow.objects.get(org=org, id=self.cleaned_data["flow_to_start"])
            obj.start_mode = self.cleaned_data["flow_start_mode"]

    def __init__(self, user, *args, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

        org = self.user.get_org()

        relative_to = self.fields["relative_to"]
        relative_to.queryset = ContactField.all_fields.filter(
            org=org, is_active=True, value_type=Value.TYPE_DATETIME
        ).order_by("label")

        flow = self.fields["flow_to_start"]
        flow.queryset = Flow.objects.filter(
            org=self.user.get_org(),
            flow_type__in=[Flow.TYPE_MESSAGE, Flow.TYPE_VOICE],
            is_active=True,
            is_archived=False,
            is_system=False,
        ).order_by("name")

        message = self.instance.message or {}
        self.languages = []

        # add in all of our languages for message forms
        languages = org.languages.all()

        for language in languages:

            insert = None

            # if it's our primary language, allow use to steal the 'base' message
            if org.primary_language and org.primary_language.iso_code == language.iso_code:

                initial = message.get(language.iso_code, "")

                if not initial:
                    initial = message.get("base", "")

                # also, let's show it first
                insert = 0
            else:

                # otherwise, its just a normal language
                initial = message.get(language.iso_code, "")

            field = forms.CharField(
                widget=CompletionTextarea(
                    attrs={
                        "placeholder": _(
                            "Hi @contact.name! This is just a friendly reminder to apply your fertilizer."
                        ),
                        "widget_only": True,
                    }
                ),
                required=False,
                label=language.name,
                initial=initial,
            )

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
            base_language = "base"

        # if we are editing, always include the flow base language
        if self.instance.id:
            base_language = self.instance.flow.base_language

        # add our default language, we'll insert it at the front of the list
        if base_language and base_language not in self.fields:
            field = forms.CharField(
                widget=forms.Textarea, required=False, label=_("Default"), initial=message.get(base_language)
            )

            self.fields[base_language] = field
            field.language = dict(iso_code=base_language, name="Default")
            self.languages.insert(0, field)

    class Meta:
        model = CampaignEvent
        fields = "__all__"
        widgets = {"offset": InputWidget(attrs={"widget_only": True})}


class CampaignEventCRUDL(SmartCRUDL):
    model = CampaignEvent
    actions = ("create", "delete", "read", "update")

    class Read(OrgObjPermsMixin, SmartReadView):
        def pre_process(self, request, *args, **kwargs):
            event = self.get_object()
            if not event.is_active:
                messages.error(self.request, "Campaign event no longer exists")
                return HttpResponseRedirect(reverse("campaigns.campaign_read", args=[event.campaign.pk]))

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            event_fires = self.get_object().fires.all()

            fired_event_fires = event_fires.exclude(fired=None).order_by("-fired", "pk")
            scheduled_event_fires = event_fires.filter(fired=None).order_by("scheduled", "pk")

            fired = fired_event_fires[:25]
            context["fired_event_fires"] = fired
            context["fired_event_fires_count"] = fired_event_fires.count() - len(fired)

            scheduled = scheduled_event_fires[:25]
            context["scheduled_event_fires"] = scheduled
            context["scheduled_event_fires_count"] = scheduled_event_fires.count() - len(scheduled)

            return context

        def get_gear_links(self):
            links = []

            campaign_event = self.get_object()

            if self.has_org_perm("campaigns.campaignevent_update") and not campaign_event.campaign.is_archived:
                links.append(
                    dict(
                        id="event-update",
                        title=_("Edit"),
                        href=reverse("campaigns.campaignevent_update", args=[campaign_event.pk]),
                        modax=_("Update Event"),
                    )
                )

            if self.has_org_perm("campaigns.campaignevent_delete"):
                links.append(
                    dict(
                        title="Delete",
                        delete=True,
                        success_url=reverse("campaigns.campaign_read", args=[campaign_event.campaign.pk]),
                        href=reverse("campaigns.campaignevent_delete", args=[campaign_event.id]),
                    )
                )

            return links

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):

        default_template = "smartmin/delete_confirm.html"

        def get_object_org(self):
            return self.get_object().campaign.org

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.release()

            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

        def get_cancel_url(self):  # pragma: needs cover
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

    class Update(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        success_message = ""
        form_class = CampaignEventForm
        submit_button_name = _("Update Event")

        default_fields = [
            "event_type",
            "flow_to_start",
            "offset",
            "unit",
            "direction",
            "relative_to",
            "delivery_hour",
            "message_start_mode",
            "flow_start_mode",
        ]

        def pre_process(self, request, *args, **kwargs):
            event = self.get_object()
            if not event.is_active or not event.campaign.is_active or event.campaign.is_archived:
                raise Http404("Event not found")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            return super().get_context_data(**kwargs)

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
            initial = super().derive_initial()

            if self.object.offset < 0:
                initial["direction"] = "B"
                initial["offset"] = abs(self.object.offset)
            else:
                initial["direction"] = "A"

            if self.object.event_type == "F":
                initial["flow_to_start"] = self.object.flow
                initial["flow_start_mode"] = self.object.start_mode
            else:
                initial["message_start_mode"] = self.object.start_mode

            return initial

        def post_save(self, obj):
            obj = super().post_save(obj)
            obj.update_flow_name()
            return obj

        def pre_save(self, obj):

            obj = super().pre_save(obj)
            self.form.pre_save(self.request, obj)

            prev = CampaignEvent.objects.get(pk=obj.pk)
            if prev.event_type == "M" and (obj.event_type == "F" and prev.flow):  # pragma: needs cover
                flow = prev.flow
                flow.is_active = False
                flow.save()
                obj.message = None

            # if we changed anything, update our event fires
            if (
                prev.unit != obj.unit
                or prev.offset != obj.offset
                or prev.relative_to != obj.relative_to
                or prev.delivery_hour != obj.delivery_hour
                or prev.message != obj.message
                or prev.flow != obj.flow
                or prev.start_mode != obj.start_mode
            ):
                obj = obj.deactivate_and_copy()
                EventFire.create_eventfires_for_event(obj)

            return obj

        def get_success_url(self):
            return reverse("campaigns.campaignevent_read", args=[self.object.pk])

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):

        default_fields = [
            "event_type",
            "flow_to_start",
            "offset",
            "unit",
            "direction",
            "relative_to",
            "delivery_hour",
            "message_start_mode",
            "flow_start_mode",
        ]
        form_class = CampaignEventForm
        success_message = ""
        template_name = "campaigns/campaignevent_update.haml"
        submit_button_name = _("Add Event")

        def pre_process(self, request, *args, **kwargs):
            campaign_id = request.GET.get("campaign", None)
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def derive_fields(self):

            from copy import deepcopy

            fields = deepcopy(self.default_fields)

            # add in all of our languages for message forms
            org = self.request.user.get_org()

            for language in org.languages.all():
                fields.append(language.iso_code)

            if not org.primary_language:
                fields.append("base")

            return fields

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.pk])

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["unit"] = "D"
            initial["offset"] = "15"
            initial["direction"] = "A"

            # default to our first date field
            initial["relative_to"] = ContactField.all_fields.filter(
                org=self.request.user.get_org(), is_active=True, value_type=Value.TYPE_DATETIME
            ).first()

            return initial

        def post_save(self, obj):
            obj = super().post_save(obj)
            obj.update_flow_name()
            EventFire.create_eventfires_for_event(obj)
            return obj

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.campaign = Campaign.objects.get(org=self.request.user.get_org(), pk=self.request.GET.get("campaign"))
            self.form.pre_save(self.request, obj)
            return obj

        def form_invalid(self, form):
            return super().form_invalid(form)
