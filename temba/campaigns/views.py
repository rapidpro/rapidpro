from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models.functions import Lower
from django.http import Http404, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import ContactField, ContactGroup
from temba.flows.models import Flow
from temba.msgs.models import Msg
from temba.orgs.views import MenuMixin, ModalMixin, OrgFilterMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils import languages
from temba.utils.fields import CompletionTextarea, InputWidget, SelectWidget, TembaChoiceField
from temba.utils.views import BulkActionMixin, ContentMenuMixin, SpaMixin

from .models import Campaign, CampaignEvent


class CampaignForm(forms.ModelForm):
    group = TembaChoiceField(
        queryset=ContactGroup.objects.none(),
        empty_label=None,
        widget=SelectWidget(attrs={"placeholder": _("Select group"), "searchable": True}),
        label=_("Group"),
        help_text=_("Only contacts in this group will be included in this campaign's events."),
    )

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.fields["group"].queryset = ContactGroup.get_groups(org).order_by(Lower("name"))

    class Meta:
        model = Campaign
        fields = ("name", "group")
        labels = {"name": _("Name")}
        widgets = {"name": InputWidget()}


class CampaignCRUDL(SmartCRUDL):
    model = Campaign
    actions = ("create", "read", "update", "list", "archived", "archive", "activate", "menu")

    class Menu(MenuMixin, SmartTemplateView):
        def derive_menu(self):
            org = self.request.org

            menu = []
            menu.append(
                self.create_menu_item(
                    menu_id="active",
                    name=_("Active"),
                    icon="campaign_active",
                    count=org.campaigns.filter(is_active=True, is_archived=False).count(),
                    href="campaigns.campaign_list",
                )
            )

            menu.append(
                self.create_menu_item(
                    menu_id="archived",
                    name=_("Archived"),
                    icon="campaign_archived",
                    count=org.campaigns.filter(is_active=True, is_archived=True).count(),
                    href="campaigns.campaign_archived",
                )
            )

            return menu

    class Update(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        fields = ("name", "group")
        form_class = CampaignForm

        def pre_process(self, request, *args, **kwargs):
            campaign_id = kwargs.get("pk")
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.uuid])

        def get_form_kwargs(self, *args, **kwargs):
            form_kwargs = super().get_form_kwargs(*args, **kwargs)
            form_kwargs["org"] = self.request.org
            return form_kwargs

        def form_valid(self, form):
            previous_group = self.get_object().group
            new_group = form.cleaned_data["group"]

            # save our campaign
            self.object = form.save(commit=False)
            self.save(self.object)

            # if our group changed, create our new fires
            if new_group != previous_group:
                self.object.recreate_events()
                self.object.schedule_events_async()

            return self.render_modal_response(form)

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        menu_path = "/campaign/active"

        def derive_title(self):
            return self.object.name

        def build_content_menu(self, menu):
            obj = self.get_object()

            if obj.is_archived:
                if self.has_org_perm("campaigns.campaign_activate"):
                    menu.add_url_post(_("Activate"), reverse("campaigns.campaign_activate", args=[obj.id]))

                if self.has_org_perm("orgs.org_export"):
                    menu.add_link(_("Export"), f"{reverse('orgs.org_export')}?campaign={obj.id}&archived=1")
            else:
                if self.has_org_perm("campaigns.campaignevent_create"):
                    menu.add_modax(
                        _("New Event"),
                        "event-add",
                        f"{reverse('campaigns.campaignevent_create')}?campaign={obj.id}",
                        as_button=True,
                    )

                if self.has_org_perm("campaigns.campaign_update"):
                    menu.add_modax(
                        _("Edit"),
                        "campaign-update",
                        reverse("campaigns.campaign_update", args=[obj.id]),
                        title=_("Edit Campaign"),
                    )

                if self.has_org_perm("orgs.org_export"):
                    menu.add_link(_("Export"), f"{reverse('orgs.org_export')}?campaign={obj.id}")

                if self.has_org_perm("campaigns.campaign_archive"):
                    menu.add_url_post(_("Archive"), reverse("campaigns.campaign_archive", args=[obj.id]))

    class Create(OrgPermsMixin, ModalMixin, SmartCreateView):
        fields = ("name", "group")
        form_class = CampaignForm
        success_url = "uuid@campaigns.campaign_read"

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.org
            return obj

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

    class BaseList(SpaMixin, ContentMenuMixin, OrgFilterMixin, OrgPermsMixin, BulkActionMixin, SmartListView):
        fields = ("name", "group")
        default_template = "campaigns/campaign_list.html"
        default_order = ("-modified_on",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_campaigns"] = self.request.org.campaigns.exists()
            context["request_url"] = self.request.path
            return context

    class List(BaseList):
        title = _("Active")
        fields = ("name", "group")
        bulk_actions = ("archive",)
        search_fields = ("name__icontains", "group__name__icontains")
        menu_path = "/campaign/active"

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=False)
            return qs

        def build_content_menu(self, menu):
            if self.has_org_perm("campaigns.campaign_create"):
                menu.add_modax(
                    _("New Campaign"),
                    "event-update",
                    reverse("campaigns.campaign_create"),
                    title=_("New Campaign"),
                    as_button=True,
                )

    class Archived(BaseList):
        title = _("Archived")
        fields = ("name",)
        bulk_actions = ("restore",)
        menu_path = "/campaign/archived"

        def get_queryset(self, *args, **kwargs):
            qs = super().get_queryset(*args, **kwargs)
            qs = qs.filter(is_active=True, is_archived=True)
            return qs

    class Archive(OrgFilterMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "uuid@campaigns.campaign_read"
        success_message = _("Campaign archived")

        def save(self, obj):
            obj.apply_action_archive(self.request.user, Campaign.objects.filter(id=obj.id))
            return obj

    class Activate(OrgFilterMixin, OrgPermsMixin, SmartUpdateView):
        fields = ()
        success_url = "uuid@campaigns.campaign_read"
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

    flow_to_start = TembaChoiceField(
        queryset=Flow.objects.filter(is_active=True),
        required=False,
        empty_label=None,
        widget=SelectWidget(
            attrs={
                "placeholder": _("Select a flow to start"),
                "widget_only": True,
                "searchable": True,
            }
        ),
    )

    relative_to = TembaChoiceField(
        queryset=ContactField.objects.none(),
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
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and run this flow")),
            (CampaignEvent.MODE_SKIP, _("Skip this event")),
        ),
        required=False,
        widget=SelectWidget(attrs={"widget_only": True}),
    )

    message_start_mode = forms.ChoiceField(
        choices=(
            (CampaignEvent.MODE_INTERRUPT, _("Stop it and send the message")),
            (CampaignEvent.MODE_SKIP, _("Skip this message")),
            (CampaignEvent.MODE_PASSIVE, _("Send the message")),
        ),
        required=False,
        widget=SelectWidget(attrs={"widget_only": True}),
    )

    def clean(self):
        data = super().clean()

        if self.data["event_type"] == CampaignEvent.TYPE_MESSAGE:
            if self.languages:
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
            if not data.get("message_start_mode"):
                self.add_error("message_start_mode", _("This field is required."))
        else:
            if not data.get("flow_to_start"):
                self.add_error("flow_to_start", _("This field is required."))
            if not data.get("flow_start_mode"):
                self.add_error("flow_start_mode", _("This field is required."))

        return data

    def pre_save(self, request, obj):
        org = request.org

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
                base_language = org.flow_languages[0]

            translations = {}
            for language in self.languages:
                iso_code = language.language["iso_code"]
                if iso_code in self.cleaned_data and self.cleaned_data.get(iso_code, "").strip():
                    translations[iso_code] = self.cleaned_data.get(iso_code, "").strip()

            if not obj.flow_id or not obj.flow.is_active or not obj.flow.is_system:
                obj.flow = Flow.create_single_message(org, request.user, translations, base_language=base_language)
            else:
                # set our single message on our flow
                obj.flow.update_single_message_flow(request.user, translations, base_language)

            obj.message = translations
            obj.full_clean()
            obj.start_mode = self.cleaned_data["message_start_mode"]

        # otherwise, it's an event that runs an existing flow
        else:
            obj.flow = self.cleaned_data["flow_to_start"]
            obj.start_mode = self.cleaned_data["flow_start_mode"]

            # force passive mode for user-selected background flows
            if obj.flow.flow_type == Flow.TYPE_BACKGROUND:
                obj.start_mode = CampaignEvent.MODE_PASSIVE

    def __init__(self, org, *args, **kwargs):
        super().__init__(*args, **kwargs)

        relative_to = self.fields["relative_to"]
        relative_to.queryset = org.fields.filter(is_active=True, value_type=ContactField.TYPE_DATETIME).order_by(
            Lower("name")
        )

        flow = self.fields["flow_to_start"]
        flow.queryset = org.flows.filter(
            flow_type__in=[Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND],
            is_active=True,
            is_archived=False,
            is_system=False,
        ).order_by("name")

        if (
            self.instance.id
            and self.instance.flow
            and self.instance.flow.flow_type == Flow.TYPE_BACKGROUND
            and not self.instance.message
        ):
            flow.widget.attrs["info_text"] = CampaignEventCRUDL.BACKGROUND_WARNING

        message = self.instance.message or {}
        self.languages = []

        # add in all of our languages for message forms
        for lang_code in org.flow_languages:
            lang_name = languages.get_name(lang_code)
            insert = None

            # if it's our primary language, allow use to steal the 'Default' message
            if org.flow_languages[0] == lang_code:
                initial = message.get(lang_code, "")

                if not initial:
                    initial = message.get("base", "") or message.get("und", "")

                # also, let's show it first
                insert = 0
            else:
                # otherwise, its just a normal language
                initial = message.get(lang_code, "")

            field = forms.CharField(
                widget=CompletionTextarea(
                    attrs={
                        "placeholder": _(
                            "Hi @contact.name! This is just a friendly reminder to apply your fertilizer."
                        ),
                        "widget_only": True,
                        "maxlength": Msg.MAX_TEXT_LEN,
                    }
                ),
                required=False,
                label=lang_name,
                initial=initial,
            )

            self.fields[lang_code] = field
            field.language = dict(name=lang_name, iso_code=lang_code)

            # see if we need to insert or append
            if insert is not None:
                self.languages.insert(insert, field)
            else:
                self.languages.append(field)

        # determine our base language if necessary
        base_language = org.flow_languages[0]

        # if we are editing, always include the flow base language
        if self.instance.id:
            base_language = self.instance.flow.base_language

        # add our default language, we'll insert it at the front of the list
        if base_language and base_language not in self.fields:
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
                label=_("Default"),
                initial=message.get(base_language),
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

    BACKGROUND_WARNING = _(
        "This is a background flow. When it triggers, it will run it for all contacts without interruption."
    )

    class Read(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<campaign_uuid>[0-9a-f-]+)/(?P<pk>\d+)/$" % (path, action)

        def derive_title(self):
            return _("Event History")

        def derive_menu_path(self):
            return f"/campaign/{'archived' if self.get_object().campaign.is_archived else 'active'}/"

        def pre_process(self, request, *args, **kwargs):
            event = self.get_object()
            if not event.is_active:
                messages.error(self.request, "Campaign event no longer exists")
                return HttpResponseRedirect(reverse("campaigns.campaign_read", args=[event.campaign.uuid]))
            return super().pre_process(request, *args, **kwargs)

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

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("campaigns.campaignevent_update") and not obj.campaign.is_archived:
                menu.add_modax(
                    _("Edit"),
                    "event-update",
                    reverse("campaigns.campaignevent_update", args=[obj.id]),
                    title=_("Edit Event"),
                )

            if self.has_org_perm("campaigns.campaignevent_delete"):
                menu.add_modax(
                    _("Delete"),
                    "event-delete",
                    reverse("campaigns.campaignevent_delete", args=[obj.id]),
                    title=_("Delete Event"),
                )

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        default_template = "smartmin/delete_confirm.html"
        submit_button_name = _("Delete")
        fields = ("uuid",)

        def get_object_org(self):
            return self.get_object().campaign.org

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.release(self.request.user)

            redirect_url = self.get_redirect_url()
            return HttpResponseRedirect(redirect_url)

        def get_redirect_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.uuid])

        def get_cancel_url(self):  # pragma: needs cover
            return reverse("campaigns.campaign_read", args=[self.object.campaign.uuid])

    class Update(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        form_class = CampaignEventForm
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
            kwargs["org"] = self.request.org
            return kwargs

        def get_object_org(self):
            return self.get_object().campaign.org

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["background_warning"] = CampaignEventCRUDL.BACKGROUND_WARNING
            return context

        def derive_fields(self):
            from copy import deepcopy

            fields = deepcopy(self.default_fields)

            # add in all of our flow languages
            org = self.request.org
            fields += org.flow_languages

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
                obj = obj.recreate()
                obj.schedule_async()

            return obj

        def get_success_url(self):
            return reverse("campaigns.campaignevent_read", args=[self.object.campaign.uuid, self.object.pk])

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
        template_name = "campaigns/campaignevent_update.html"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["background_warning"] = CampaignEventCRUDL.BACKGROUND_WARNING
            return context

        def pre_process(self, request, *args, **kwargs):
            campaign_id = request.GET.get("campaign", None)
            if campaign_id:
                campaign = Campaign.objects.filter(id=campaign_id, is_active=True, is_archived=False)

                if not campaign.exists():
                    raise Http404("Campaign not found")

        def derive_fields(self):
            from copy import deepcopy

            fields = deepcopy(self.default_fields)

            # add in all of our flow languages
            fields += self.request.org.flow_languages

            return fields

        def get_success_url(self):
            return reverse("campaigns.campaign_read", args=[self.object.campaign.uuid])

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            initial["unit"] = "D"
            initial["offset"] = "15"
            initial["direction"] = "A"
            initial["event_type"] = "M"
            initial["message_start_mode"] = "I"
            initial["delivery_hour"] = "-1"

            # default to our first date field
            initial["relative_to"] = self.request.org.fields.filter(
                is_active=True, value_type=ContactField.TYPE_DATETIME
            ).first()

            return initial

        def post_save(self, obj):
            obj = super().post_save(obj)
            obj.update_flow_name()
            obj.schedule_async()
            return obj

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.campaign = Campaign.objects.get(org=self.request.org, id=self.request.GET.get("campaign"))
            self.form.pre_save(self.request, obj)
            return obj

        def form_invalid(self, form):
            return super().form_invalid(form)
