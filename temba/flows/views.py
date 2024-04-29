import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import iso8601
import regex
import requests
from packaging.version import Version
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
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Min, Sum
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView

from temba import mailroom
from temba.archives.models import Archive
from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.contacts.search import SearchException, parse_query
from temba.flows.models import Flow, FlowRevision, FlowRun, FlowSession, FlowStart
from temba.flows.tasks import export_flow_results_task, update_session_wait_expires
from temba.ivr.models import Call
from temba.mailroom import FlowValidationException
from temba.orgs.models import IntegrationType, Org
from temba.orgs.views import (
    DependencyDeleteModal,
    MenuMixin,
    ModalMixin,
    OrgFilterMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.triggers.models import Trigger
from temba.utils import analytics, gettext, json, languages, on_transaction_commit, str_to_bool
from temba.utils.export.views import BaseExportView
from temba.utils.fields import (
    CheckboxWidget,
    ContactSearchWidget,
    InputWidget,
    OmniboxChoice,
    OmniboxField,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
)
from temba.utils.text import slugify_with
from temba.utils.views import BulkActionMixin, ContentMenuMixin, SpaMixin, StaffOnlyMixin

from .models import (
    ExportFlowResultsTask,
    FlowLabel,
    FlowStartCount,
    FlowUserConflictException,
    FlowVersionConflictException,
)

logger = logging.getLogger(__name__)


class BaseFlowForm(forms.ModelForm):
    def __init__(self, org, branding, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org
        self.branding = branding

    def clean_name(self):
        name = self.cleaned_data["name"]

        # make sure the name isn't already taken
        existing = self.org.flows.filter(is_active=True, name__iexact=name).first()
        if existing and self.instance != existing:
            # TODO include link to flow, requires https://github.com/nyaruka/temba-components/issues/159
            # existing_url = reverse("flows.flow_editor", args=[existing.uuid])
            # mark_safe(_('Already used by <a href="%(url)s">another flow</a>.') % {"url": existing_url})
            raise forms.ValidationError(_("Already used by another flow."))

        return name

    def clean_keyword_triggers(self):
        value = self.data.getlist("keyword_triggers", [])

        duplicates = []
        wrong_format = []
        cleaned_keywords = []

        for keyword in value:
            keyword = keyword.lower().strip()
            if not keyword:  # pragma: needs cover
                continue

            if (
                not regex.match(r"^\w+$", keyword, flags=regex.UNICODE | regex.V0)
                or len(keyword) > Trigger.KEYWORD_MAX_LEN
            ):
                wrong_format.append(keyword)

            # make sure it won't conflict with existing triggers
            conflicts = Trigger.get_conflicts(self.org, Trigger.TYPE_KEYWORD, keyword=keyword)
            if self.instance:
                conflicts = conflicts.exclude(flow=self.instance.id)

            if conflicts:
                duplicates.append(keyword)
            else:
                cleaned_keywords.append(keyword)

        if wrong_format:
            raise forms.ValidationError(
                _("Must be single words, less than %(limit)d characters, containing only letters and numbers.")
                % {"limit": Trigger.KEYWORD_MAX_LEN}
            )

        if duplicates:
            joined = ", ".join([f'"{k}"' for k in duplicates])
            if len(duplicates) > 1:
                error_message = _("%(keywords)s are already used for another flow.") % {"keywords": joined}
            else:
                error_message = _("%(keyword)s is already used for another flow.") % {"keyword": joined}
            raise forms.ValidationError(error_message)

        return ",".join(cleaned_keywords)

    class Meta:
        model = Flow
        fields = "__all__"


class PartialTemplate(SmartTemplateView):  # pragma: no cover
    def pre_process(self, request, *args, **kwargs):
        self.template = kwargs["template"]
        return

    def get_template_names(self):
        return "partials/%s.html" % self.template


class FlowSessionCRUDL(SmartCRUDL):
    actions = ("json",)
    model = FlowSession

    class Json(StaffOnlyMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get(self, request, *args, **kwargs):
            session = self.get_object()
            output = session.output_json
            output["_metadata"] = dict(
                session_id=session.id, org=session.org.name, org_id=session.org_id, site=self.request.branding["link"]
            )
            return JsonResponse(output, json_dumps_params=dict(indent=2))


class FlowRunCRUDL(SmartCRUDL):
    actions = ("delete",)
    model = FlowRun

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("pk",)
        success_message = None

        def post(self, request, *args, **kwargs):
            self.get_object().delete()
            return HttpResponse()


class FlowCRUDL(SmartCRUDL):
    actions = (
        "list",
        "archived",
        "copy",
        "create",
        "delete",
        "update",
        "menu",
        "simulate",
        "change_language",
        "export_translation",
        "download_translation",
        "import_translation",
        "export_results",
        "editor",
        "results",
        "run_table",
        "category_counts",
        "preview_start",
        "broadcast",
        "activity",
        "activity_chart",
        "filter",
        "campaign",
        "revisions",
        "recent_contacts",
        "assets",
    )

    model = Flow

    class AllowOnlyActiveFlowMixin:
        def get_queryset(self):
            initial_queryset = super().get_queryset()
            return initial_queryset.filter(is_active=True)

    class Menu(MenuMixin, SmartTemplateView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/((?P<submenu>[A-z]+)/)?$" % (path, action)

        def derive_menu(self):
            labels = FlowLabel.objects.filter(org=self.request.org, parent=None).order_by("name")

            menu = []
            menu.append(
                self.create_menu_item(
                    name=_("Active"), verbose_name=_("Active Flows"), icon="icon.active", href="flows.flow_list"
                )
            )
            menu.append(
                self.create_menu_item(
                    name=_("Archived"),
                    verbose_name=_("Archived Flows"),
                    icon="icon.archive",
                    href="flows.flow_archived",
                )
            )

            label_items = []
            for label in labels:
                label_items.append(
                    self.create_menu_item(
                        icon="icon.label",
                        menu_id=label.uuid,
                        name=label.name,
                        href=reverse("flows.flow_filter", args=[label.uuid]),
                        count=label.get_flow_count(),
                    )
                )

            if label_items:
                menu.append(self.create_menu_item(name=_("Labels"), items=label_items, inline=True))

            return menu

    class RecentContacts(OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for the rollover of recent contacts coming out of a split
        """

        slug_url_kwarg = "uuid"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<uuid>[0-9a-f-]+)/(?P<exit_uuid>[0-9a-f-]+)/(?P<dest_uuid>[0-9a-f-]+)/$"

        def render_to_response(self, context, **response_kwargs):
            exit_uuid, dest_uuid = self.kwargs["exit_uuid"], self.kwargs["dest_uuid"]

            return JsonResponse(self.object.get_recent_contacts(exit_uuid, dest_uuid), safe=False)

    class Revisions(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for fetching and saving flow definitions
        """

        slug_url_kwarg = "uuid"

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<uuid>[0-9a-f-]+)/((?P<revision_id>\d+)/)?$" % (path, action)

        def get(self, request, *args, **kwargs):
            flow = self.get_object()
            revision_id = self.kwargs.get("revision_id")

            # the editor requests the spec version it supports which allows us to add support for new versions
            # on the goflow/mailroom side before updating the editor to use that new version
            requested_version = request.GET.get("version", Flow.CURRENT_SPEC_VERSION)

            # we are looking for a specific revision, fetch it and migrate it forward
            if revision_id:
                revision = FlowRevision.objects.get(flow=flow, id=revision_id)
                definition = revision.get_migrated_definition(to_version=requested_version)

                # get our metadata
                flow_info = mailroom.get_client().flow_inspect(flow.org_id, definition)
                return JsonResponse(
                    {
                        "definition": definition,
                        "issues": flow_info[Flow.INSPECT_ISSUES],
                        "metadata": Flow.get_metadata(flow_info),
                    }
                )

            # build a list of valid revisions to display
            revisions = []

            for revision in flow.revisions.all().order_by("-revision")[:100]:
                revision_version = Version(revision.spec_version)

                # our goflow revisions are already validated
                if revision_version >= Version(Flow.INITIAL_GOFLOW_VERSION):
                    revisions.append(revision.as_json())
                    continue

                # legacy revisions should be validated first as a failsafe
                try:
                    legacy_flow_def = revision.get_migrated_definition(to_version=Flow.FINAL_LEGACY_VERSION)
                    FlowRevision.validate_legacy_definition(legacy_flow_def)
                    revisions.append(revision.as_json())

                except ValueError:
                    # "expected" error in the def, silently cull it
                    pass

                except Exception as e:
                    # something else, we still cull, but report it to sentry
                    logger.error(
                        f"Error validating flow revision ({flow.uuid} [{revision.id}]): {str(e)}", exc_info=True
                    )
                    pass

            return JsonResponse({"results": revisions}, safe=False)

        def post(self, request, *args, **kwargs):
            if not self.has_org_perm("flows.flow_update"):
                return JsonResponse(
                    {"status": "failure", "description": _("You don't have permission to edit this flow")}, status=403
                )

            # try to parse our body
            definition = json.loads(force_str(request.body))
            try:
                flow = self.get_object(self.get_queryset())
                revision, issues = flow.save_revision(self.request.user, definition)
                return JsonResponse(
                    {
                        "status": "success",
                        "saved_on": json.encode_datetime(flow.saved_on, micros=True),
                        "revision": revision.as_json(),
                        "issues": issues,
                        "metadata": flow.metadata,
                    }
                )

            except FlowValidationException as e:
                error = _("Your flow failed validation. Please refresh your browser.")
                detail = str(e)
            except FlowVersionConflictException:
                error = _(
                    "Your flow has been upgraded to the latest version. "
                    "In order to continue editing, please refresh your browser."
                )
                detail = None
            except FlowUserConflictException as e:
                error = (
                    _(
                        "%s is currently editing this Flow. "
                        "Your changes will not be saved until you refresh your browser."
                    )
                    % e.other_user
                )
                detail = None
            except Exception as e:  # pragma: no cover
                import traceback

                traceback.print_stack(e)
                error = _("Your flow could not be saved. Please refresh your browser.")
                detail = None

            return JsonResponse({"status": "failure", "description": error, "detail": detail}, status=400)

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        class Form(BaseFlowForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Select keywords to trigger this flow"),
                    }
                ),
            )

            flow_type = forms.ChoiceField(
                label=_("Type"),
                help_text=_("Choose the method for your flow"),
                choices=Flow.TYPE_CHOICES,
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, org, branding, *args, **kwargs):
                super().__init__(org, branding, *args, **kwargs)

                language_choices = languages.choices(org.flow_languages)

                self.fields["base_language"] = forms.ChoiceField(
                    label=_("Language"),
                    initial=org.flow_languages[0],
                    choices=language_choices,
                    widget=SelectWidget(attrs={"widget_only": False}),
                )

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "flow_type", "base_language")
                widgets = {"name": InputWidget()}

        form_class = Form
        success_url = "uuid@flows.flow_editor"
        success_message = ""
        field_config = {"name": {"help": _("Choose a unique name to describe this flow, e.g. Registration")}}

        def derive_exclude(self):
            return ["base_language"] if not self.request.org.flow_languages else []

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["branding"] = self.request.branding
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["has_flows"] = self.request.org.flows.filter(is_active=True).count() > 0
            return context

        def save(self, obj):
            self.object = Flow.create(
                self.request.org,
                self.request.user,
                obj.name,
                flow_type=obj.flow_type,
                expires_after_minutes=Flow.EXPIRES_DEFAULTS[obj.flow_type],
                base_language=obj.base_language,
                create_revision=True,
            )

        def post_save(self, obj):
            user = self.request.user
            org = self.request.org

            # create any triggers if user provided keywords
            if self.form.cleaned_data["keyword_triggers"]:
                keywords = self.form.cleaned_data["keyword_triggers"].split(",")
                for keyword in keywords:
                    Trigger.create(org, user, Trigger.TYPE_KEYWORD, flow=obj, keyword=keyword)

            return obj

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@flows.flow_editor"
        success_url = "@flows.flow_list"
        success_message = ""

    class Copy(OrgObjPermsMixin, SmartUpdateView):
        fields = []
        success_message = ""

        def form_valid(self, form):
            copy = self.object.clone(self.request.user)

            # redirect to the newly created flow
            return HttpResponseRedirect(reverse("flows.flow_editor", args=[copy.uuid]))

    class Update(AllowOnlyActiveFlowMixin, ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        class BaseForm(BaseFlowForm):
            class Meta:
                model = Flow
                fields = ("name",)
                widgets = {"name": InputWidget()}

        class SurveyForm(BaseForm):
            contact_creation = forms.ChoiceField(
                label=_("Create a contact "),
                help_text=_("Whether surveyor logins should be used as the contact for each run"),
                choices=((Flow.CONTACT_PER_RUN, _("For each run")), (Flow.CONTACT_PER_LOGIN, _("For each login"))),
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["contact_creation"].initial = self.instance.metadata.get(
                    Flow.CONTACT_CREATION, Flow.CONTACT_PER_RUN
                )

            class Meta:
                model = Flow
                fields = ("name", "contact_creation")
                widgets = {"name": InputWidget()}

        class VoiceForm(BaseForm):
            ivr_retry = forms.ChoiceField(
                label=_("Retry call if unable to connect"),
                help_text=_("Retries call three times for the chosen interval"),
                initial=60,
                choices=Call.RETRY_CHOICES,
                widget=SelectWidget(attrs={"widget_only": False}),
            )
            expires_after_minutes = forms.ChoiceField(
                label=_("Expire inactive contacts"),
                help_text=_("When inactive contacts should be removed from the flow"),
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_VOICE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_VOICE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Keywords"),
                    }
                ),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                metadata = self.instance.metadata

                # IVR retries
                ivr_retry = self.fields["ivr_retry"]
                ivr_retry.initial = metadata.get("ivr_retry", self.fields["ivr_retry"].initial)

                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org,
                    flow=self.instance,
                    is_archived=False,
                    groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD,
                ).order_by("created_on")

                keyword_triggers = self.fields["keyword_triggers"]
                keyword_triggers.initial = ",".join(t.keyword for t in flow_triggers)

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

        class MessagingForm(BaseForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Global keyword triggers"),
                help_text=_("When a user sends any of these keywords they will begin this flow"),
                widget=SelectWidget(
                    attrs={
                        "widget_only": False,
                        "multi": True,
                        "searchable": True,
                        "tags": True,
                        "space_select": True,
                        "placeholder": _("Keywords"),
                    }
                ),
            )

            expires_after_minutes = forms.ChoiceField(
                label=_("Expire inactive contacts"),
                help_text=_("When inactive contacts should be removed from the flow"),
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_MESSAGE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_MESSAGE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                flow_triggers = Trigger.objects.filter(
                    org=self.instance.org,
                    flow=self.instance,
                    is_archived=False,
                    groups=None,
                    trigger_type=Trigger.TYPE_KEYWORD,
                ).order_by("created_on")

                keyword_triggers = self.fields["keyword_triggers"]
                keyword_triggers.initial = list([t.keyword for t in flow_triggers])

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

        success_message = ""
        success_url = "uuid@flows.flow_editor"
        form_classes = {
            Flow.TYPE_MESSAGE: MessagingForm,
            Flow.TYPE_VOICE: VoiceForm,
            Flow.TYPE_SURVEY: SurveyForm,
            Flow.TYPE_BACKGROUND: BaseForm,
        }

        def get_form_class(self):
            return self.form_classes[self.object.flow_type]

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["branding"] = self.request.branding
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            metadata = obj.metadata

            if Flow.CONTACT_CREATION in self.form.cleaned_data:
                metadata[Flow.CONTACT_CREATION] = self.form.cleaned_data[Flow.CONTACT_CREATION]

            if "ivr_retry" in self.form.cleaned_data:
                metadata[Flow.METADATA_IVR_RETRY] = int(self.form.cleaned_data["ivr_retry"])

            obj.metadata = metadata
            return obj

        def post_save(self, obj):
            keywords = set()
            user = self.request.user
            org = self.request.org

            if "keyword_triggers" in self.form.cleaned_data:
                # get existing keyword triggers for this flow
                existing = obj.triggers.filter(trigger_type=Trigger.TYPE_KEYWORD, is_archived=False, groups=None)
                existing_keywords = {t.keyword for t in existing}

                if len(self.form.cleaned_data["keyword_triggers"]) > 0:
                    keywords = set(self.form.cleaned_data["keyword_triggers"].split(","))

                removed_keywords = existing_keywords.difference(keywords)
                for keyword in removed_keywords:
                    obj.triggers.filter(keyword=keyword, groups=None, is_archived=False).update(is_archived=True)

                added_keywords = keywords.difference(existing_keywords)
                archived_keywords = [
                    t.keyword
                    for t in obj.triggers.filter(
                        org=org, flow=obj, trigger_type=Trigger.TYPE_KEYWORD, is_archived=True, groups=None
                    )
                ]

                # set difference does not have a deterministic order, we need to sort the keywords
                for keyword in sorted(added_keywords):
                    # first check if the added keyword is not amongst archived
                    if keyword in archived_keywords:  # pragma: needs cover
                        obj.triggers.filter(org=org, flow=obj, keyword=keyword, groups=None).update(is_archived=False)
                    else:
                        Trigger.objects.create(
                            org=org,
                            keyword=keyword,
                            trigger_type=Trigger.TYPE_KEYWORD,
                            flow=obj,
                            created_by=user,
                            modified_by=user,
                        )

            on_transaction_commit(lambda: update_session_wait_expires.delay(obj.pk))

            return obj

    class BaseList(SpaMixin, OrgFilterMixin, OrgPermsMixin, BulkActionMixin, ContentMenuMixin, SmartListView):
        title = _("Flows")
        refresh = 10000
        fields = ("name", "modified_on")
        default_template = "flows/flow_list.html"
        default_order = ("-saved_on",)
        search_fields = ("name__icontains",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["org_has_flows"] = self.request.org.flows.filter(is_active=True).exists()
            context["folders"] = self.get_folders()
            context["labels"] = self.get_flow_labels()
            context["campaigns"] = self.get_campaigns()
            context["request_url"] = self.request.path

            # decorate flow objects with their run activity stats
            for flow in context["object_list"]:
                flow.run_stats = flow.get_run_stats()

            return context

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)
            return qs.exclude(is_system=True).exclude(is_active=False)

        def get_campaigns(self):
            from temba.campaigns.models import CampaignEvent

            org = self.request.org
            events = CampaignEvent.objects.filter(
                campaign__org=org,
                is_active=True,
                campaign__is_active=True,
                flow__is_archived=False,
                flow__is_active=True,
                flow__is_system=False,
            )
            return (
                events.values("campaign__name", "campaign__id").annotate(count=Count("id")).order_by("campaign__name")
            )

        def apply_bulk_action(self, user, action, objects, label):
            super().apply_bulk_action(user, action, objects, label)

            if action == "archive":
                ignored = objects.filter(is_archived=False)
                if ignored:
                    flow_names = ", ".join([f.name for f in ignored])
                    raise forms.ValidationError(
                        _("The following flows are still used by campaigns so could not be archived: %(flows)s"),
                        params={"flows": flow_names},
                    )

        def get_bulk_action_labels(self):
            return self.request.org.flow_labels.filter(is_active=True)

        def get_flow_labels(self):
            labels = []
            for label in self.request.org.flow_labels.order_by("name"):
                labels.append(
                    {
                        "id": label.id,
                        "uuid": label.uuid,
                        "name": label.name,
                        "count": label.get_flow_count(),
                    }
                )
            return labels

        def get_folders(self):
            org = self.request.org

            return [
                dict(
                    label="Active",
                    url=reverse("flows.flow_list"),
                    count=Flow.objects.exclude(is_system=True)
                    .filter(is_active=True, is_archived=False, org=org)
                    .count(),
                ),
                dict(
                    label="Archived",
                    url=reverse("flows.flow_archived"),
                    count=Flow.objects.exclude(is_system=True)
                    .filter(is_active=True, is_archived=True, org=org)
                    .count(),
                ),
            ]

        def build_content_menu(self, menu):
            if self.is_spa():
                if self.has_org_perm("flows.flow_create"):
                    menu.add_modax(
                        _("New Flow"),
                        "new-flow",
                        f"{reverse('flows.flow_create')}",
                        title=_("New Flow"),
                        primary=True,
                        as_button=True,
                    )

                if self.has_org_perm("flows.flowlabel_create"):
                    menu.add_modax(
                        _("New Label"),
                        "new-flow-label",
                        f"{reverse('flows.flowlabel_create')}",
                        title=_("New Label"),
                        on_submit="handleCreateLabelModalSubmitted()",
                    )

            if self.has_org_perm("orgs.org_import"):
                menu.add_link(_("Import"), reverse("orgs.org_import"))
            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export"), reverse("orgs.org_export"))

    class Archived(BaseList):
        title = _("Archived Flows")
        bulk_actions = ("restore",)
        default_order = ("-created_on",)

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Active Flows")
        bulk_actions = ("archive", "label", "download-results")

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            return queryset

    class Campaign(BaseList, OrgObjPermsMixin):
        bulk_actions = ("label",)
        campaign = None

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<campaign_id>\d+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.get_campaign().name

        def get_object_org(self):
            from temba.campaigns.models import Campaign

            return Campaign.objects.get(pk=self.kwargs["campaign_id"]).org

        def get_campaign(self):
            if not self.campaign:
                from temba.campaigns.models import Campaign

                campaign_id = self.kwargs["campaign_id"]
                self.campaign = Campaign.objects.filter(id=campaign_id, org=self.request.org).first()
            return self.campaign

        def get_queryset(self, **kwargs):
            from temba.campaigns.models import CampaignEvent

            flow_ids = CampaignEvent.objects.filter(
                campaign=self.get_campaign(), flow__is_archived=False, flow__is_system=False
            ).values("flow__id")

            flows = Flow.objects.filter(id__in=flow_ids, org=self.request.org).order_by("-modified_on")
            return flows

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["current_campaign"] = self.get_campaign()
            return context

    class Filter(BaseList, OrgObjPermsMixin):
        add_button = True
        bulk_actions = ("label",)
        slug_url_kwarg = "uuid"

        def build_content_menu(self, menu):
            if self.has_org_perm("flows.flow_update"):
                menu.add_modax(
                    _("Edit"),
                    "update-label",
                    f"{reverse('flows.flowlabel_update', args=[self.label.id])}",
                    title=_("Edit Label"),
                    primary=True,
                )

            if self.has_org_perm("flows.flow_delete"):
                menu.add_modax(
                    _("Delete Label"), "delete-label", f"{reverse('flows.flowlabel_delete', args=[self.label.id])}"
                )

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["current_label"] = self.label
            return context

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<label_uuid>[0-9a-f-]+)/$" % (path, action)

        def derive_title(self, *args, **kwargs):
            return self.label.name

        def get_object_org(self):
            return self.label.org

        @cached_property
        def label(self):
            return FlowLabel.objects.get(uuid=self.kwargs["label_uuid"], org=self.request.org)

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return qs.filter(org=self.request.org, labels=self.label, is_archived=False).order_by("-created_on")

    class Editor(SpaMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def derive_title(self):
            return self.object.name

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            if not self.is_spa():
                dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
                prefix = "/dev" if dev_mode else settings.STATIC_URL

                # get our list of assets to incude
                scripts = []
                styles = []

                if dev_mode:  # pragma: no cover
                    response = requests.get("http://localhost:3000/asset-manifest.json")
                    data = response.json()
                else:
                    with open("node_modules/@nyaruka/flow-editor/build/asset-manifest.json") as json_file:
                        data = json.load(json_file)

                for key, filename in data.get("files").items():

                    # tack on our prefix for dev mode
                    filename = prefix + filename

                    # ignore precache manifest
                    if key.startswith("precache-manifest") or key.startswith("service-worker"):
                        continue

                    # css files
                    if key.endswith(".css") and filename.endswith(".css"):
                        styles.append(filename)

                    # javascript
                    if key.endswith(".js") and filename.endswith(".js"):
                        scripts.append(filename)

                context["scripts"] = scripts
                context["styles"] = styles
                context["dev_mode"] = dev_mode

            flow = self.object

            context["migrate"] = "migrate" in self.request.GET

            if flow.is_archived:
                context["mutable"] = False
                context["can_start"] = False
                context["can_simulate"] = False
            else:
                context["mutable"] = self.has_org_perm("flows.flow_update")
                context["can_start"] = flow.flow_type != Flow.TYPE_VOICE or flow.org.supports_ivr()
                context["can_simulate"] = True

            context["is_starting"] = flow.is_starting()
            context["feature_filters"] = json.dumps(self.get_features(flow.org))
            return context

        def get_features(self, org) -> list:
            features = []

            facebook_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.FACEBOOK_SCHEME)
            whatsapp_channel = org.get_channel(Channel.ROLE_SEND, scheme=URN.WHATSAPP_SCHEME)

            if facebook_channel:
                features.append("facebook")
            if whatsapp_channel:
                features.append("whatsapp")
            if org.get_integrations(IntegrationType.Category.AIRTIME):
                features.append("airtime")
            if org.classifiers.filter(is_active=True).exists():
                features.append("classifier")
            if org.ticketers.filter(is_active=True).exists():
                features.append("ticketer")
            if org.get_resthooks():
                features.append("resthook")
            if org.country_id:
                features.append("locations")

            return features

        def build_content_menu(self, menu):
            obj = self.get_object()

            if obj.flow_type != Flow.TYPE_SURVEY and self.has_org_perm("flows.flow_broadcast") and not obj.is_archived:
                menu.add_modax(
                    _("Start Flow"),
                    "start-flow",
                    f"{reverse('flows.flow_broadcast', args=[])}?flow={obj.id}",
                    primary=True,
                    as_button=True,
                    disabled=True,
                )

            if self.has_org_perm("flows.flow_results"):
                menu.add_link(_("Results"), reverse("flows.flow_results", args=[obj.uuid]))

            menu.new_group()

            if self.has_org_perm("flows.flow_update") and not obj.is_archived:
                menu.add_modax(
                    _("Edit"),
                    "edit-flow",
                    f"{reverse('flows.flow_update', args=[obj.id])}",
                    title=_("Edit Flow"),
                )

            if self.has_org_perm("flows.flow_copy"):
                menu.add_url_post(_("Copy"), reverse("flows.flow_copy", args=[obj.id]))

            if self.has_org_perm("flows.flow_delete"):
                menu.add_modax(
                    _("Delete"),
                    "delete-flow",
                    reverse("flows.flow_delete", args=[obj.uuid]),
                    title=_("Delete Flow"),
                )

            menu.new_group()

            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export Definition"), f"{reverse('orgs.org_export')}?flow={obj.id}")

            # limit PO export/import to non-archived flows since mailroom doesn't know about archived flows
            if not obj.is_archived:
                if self.has_org_perm("flows.flow_export_translation"):
                    menu.add_modax(
                        _("Export Translation"),
                        "export-translation",
                        reverse("flows.flow_export_translation", args=[obj.id]),
                    )

                if self.has_org_perm("flows.flow_import_translation"):
                    menu.add_link(_("Import Translation"), reverse("flows.flow_import_translation", args=[obj.id]))

            if self.request.user.is_staff:
                menu.new_group()
                menu.add_url_post(
                    _("Service"),
                    f'{reverse("orgs.org_service")}?organization={obj.org_id}&redirect_url={reverse("flows.flow_editor", args=[obj.uuid])}',
                )

    class ChangeLanguage(OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.Form):
            language = forms.CharField(required=True)

            def __init__(self, org, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.org = org

            def clean_language(self):
                data = self.cleaned_data["language"]
                if data and data not in self.org.flow_languages:
                    raise ValidationError(_("Not a valid language."))

                return data

        form_class = Form
        success_url = "uuid@flows.flow_editor"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            flow_def = mailroom.get_client().flow_change_language(
                self.object.get_definition(), form.cleaned_data["language"]
            )

            self.object.save_revision(self.request.user, flow_def)

            return HttpResponseRedirect(self.get_success_url())

    class ExportTranslation(OrgObjPermsMixin, ModalMixin, SmartUpdateView):
        class Form(forms.Form):
            language = forms.ChoiceField(
                required=False,
                label=_("Language"),
                help_text=_("Include translations in this language."),
                choices=(("", "None"),),
                widget=SelectWidget(),
            )

            def __init__(self, org, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["language"].choices += languages.choices(codes=org.flow_languages)

        form_class = Form
        submit_button_name = _("Export")
        success_url = "@flows.flow_list"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            params = {"flow": self.object.id, "language": form.cleaned_data["language"]}
            download_url = reverse("flows.flow_download_translation") + "?" + urlencode(params, doseq=True)

            # if this is an XHR request, we need to return a structured response that it can parse
            if "HTTP_X_PJAX" in self.request.META:
                response = self.render_modal_response(form)
                response["Temba-Success"] = download_url
                return response

            return HttpResponseRedirect(download_url)

    class DownloadTranslation(OrgPermsMixin, SmartListView):
        """
        Download link for PO translation files extracted from flows by mailroom
        """

        def get(self, request, *args, **kwargs):
            org = self.request.org
            flow_ids = self.request.GET.getlist("flow")
            flows = org.flows.filter(id__in=flow_ids, is_active=True)
            if len(flows) != len(flow_ids):
                raise Http404()

            language = request.GET.get("language", "")
            filename = slugify_with(flows[0].name) if len(flows) == 1 else "flows"
            if language:
                filename += f".{language}"
            filename += ".po"

            po = Flow.export_translation(org, flows, language)

            response = HttpResponse(po, content_type="text/x-gettext-translation")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

    class ImportTranslation(OrgObjPermsMixin, SmartUpdateView):
        class UploadForm(forms.Form):
            po_file = forms.FileField(label=_("PO translation file"), required=True)

            def __init__(self, org, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.flow = instance

            def clean_po_file(self):
                data = self.cleaned_data["po_file"]
                if data:
                    try:
                        po_info = gettext.po_get_info(data.read().decode())
                    except Exception:
                        raise ValidationError(_("File doesn't appear to be a valid PO file."))

                    if po_info.language_code:
                        if po_info.language_code == self.flow.base_language:
                            raise ValidationError(
                                _("Contains translations in %(lang)s which is the base language of this flow."),
                                params={"lang": po_info.language_name},
                            )

                        if po_info.language_code not in self.flow.org.flow_languages:
                            raise ValidationError(
                                _("Contains translations in %(lang)s which is not a supported translation language."),
                                params={"lang": po_info.language_name},
                            )

                return data

        class ConfirmForm(forms.Form):
            language = forms.ChoiceField(
                label=_("Language"),
                help_text=_("Replace flow translations in this language."),
                required=True,
                widget=SelectWidget(),
            )

            def __init__(self, org, instance, *args, **kwargs):
                super().__init__(*args, **kwargs)

                lang_codes = list(org.flow_languages)
                lang_codes.remove(instance.base_language)

                self.fields["language"].choices = languages.choices(codes=lang_codes)

        title = _("Import Translation")
        submit_button_name = _("Import")
        success_url = "uuid@flows.flow_editor"

        def get_form_class(self):
            return self.ConfirmForm if self.request.GET.get("po") else self.UploadForm

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            org = self.request.org
            po_uuid = self.request.GET.get("po")

            if not po_uuid:
                po_file = form.cleaned_data["po_file"]
                po_uuid = gettext.po_save(org, po_file)

                return HttpResponseRedirect(
                    reverse("flows.flow_import_translation", args=[self.object.id]) + f"?po={po_uuid}"
                )
            else:
                po_data = gettext.po_load(org, po_uuid)
                language = form.cleaned_data["language"]

                updated_defs = Flow.import_translation(self.object.org, [self.object], language, po_data)
                self.object.save_revision(self.request.user, updated_defs[str(self.object.uuid)])

                analytics.track(self.request.user, "temba.flow_po_imported")

            return HttpResponseRedirect(self.get_success_url())

        @cached_property
        def po_info(self):
            po_uuid = self.request.GET.get("po")
            if not po_uuid:
                return None

            org = self.request.org
            po_data = gettext.po_load(org, po_uuid)
            return gettext.po_get_info(po_data)

        def get_context_data(self, *args, **kwargs):
            flow_lang_code = self.object.base_language

            context = super().get_context_data(*args, **kwargs)
            context["show_upload_form"] = not self.po_info
            context["po_info"] = self.po_info
            context["flow_language"] = {"iso_code": flow_lang_code, "name": languages.get_name(flow_lang_code)}
            return context

        def derive_initial(self):
            return {"language": self.po_info.language_code if self.po_info else ""}

    class ExportResults(BaseExportView):
        class Form(BaseExportView.Form):
            flows = forms.ModelMultipleChoiceField(
                Flow.objects.filter(id__lt=0), required=True, widget=forms.MultipleHiddenInput()
            )

            extra_urns = forms.MultipleChoiceField(
                required=False,
                label=_("URNs"),
                choices=URN.SCHEME_CHOICES,
                widget=SelectMultipleWidget(
                    attrs={"placeholder": _("Optional: URNs in addition to the one used in the flow")}
                ),
            )

            responded_only = forms.BooleanField(
                required=False,
                label=_("Responded Only"),
                initial=True,
                help_text=_("Only export results for contacts which responded"),
                widget=CheckboxWidget(),
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(org, *args, **kwargs)

                self.fields["flows"].queryset = Flow.objects.filter(org=org, is_active=True)

        form_class = Form
        success_url = "@flows.flow_list"

        def derive_initial(self):
            initial = super().derive_initial()

            flow_ids = self.request.GET.get("ids", None)
            if flow_ids:  # pragma: needs cover
                initial["flows"] = self.request.org.flows.filter(is_active=True, id__in=flow_ids.split(","))

            return initial

        def derive_exclude(self):
            return ["extra_urns"] if self.request.org.is_anon else []

        def form_valid(self, form):
            user = self.request.user
            org = self.request.org

            # is there already an export taking place?
            existing = ExportFlowResultsTask.get_recent_unfinished(org)
            if existing:
                messages.info(
                    self.request,
                    _(
                        "There is already an export in progress, started by %s. You must wait "
                        "for that export to complete before starting another." % existing.created_by.username
                    ),
                )
            else:
                flows = form.cleaned_data["flows"]
                responded_only = form.cleaned_data[ExportFlowResultsTask.RESPONDED_ONLY]

                export = ExportFlowResultsTask.create(
                    org,
                    user,
                    start_date=form.cleaned_data["start_date"],
                    end_date=form.cleaned_data["end_date"],
                    flows=flows,
                    with_fields=form.cleaned_data["with_fields"],
                    with_groups=form.cleaned_data["with_groups"],
                    responded_only=responded_only,
                    extra_urns=form.cleaned_data.get(ExportFlowResultsTask.EXTRA_URNS, []),
                )
                on_transaction_commit(lambda: export_flow_results_task.delay(export.pk))

                analytics.track(
                    self.request.user,
                    "temba.responses_export_started" if responded_only else "temba.results_export_started",
                    dict(flows=", ".join([f.uuid for f in flows])),
                )

                if not getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):  # pragma: needs cover
                    messages.info(
                        self.request,
                        _("We are preparing your export. We will e-mail you at %s when it is ready.")
                        % self.request.user.username,
                    )

                else:
                    export = ExportFlowResultsTask.objects.get(id=export.id)
                    dl_url = reverse("assets.download", kwargs=dict(type="results_export", pk=export.id))
                    messages.info(
                        self.request,
                        _("Export complete, you can find it here: %s (production users will get an email)") % dl_url,
                    )

            response = self.render_modal_response(form)
            response["REDIRECT"] = self.get_success_url()
            return response

    class ActivityChart(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper that renders a chart of activity by a given period
        """

        # the min number of responses to show a histogram
        HISTOGRAM_MIN = 0

        # the min number of responses to show the period charts
        PERIOD_MIN = 0

        def get_context_data(self, *args, **kwargs):

            total_responses = 0
            context = super().get_context_data(*args, **kwargs)

            flow = self.get_object()
            from temba.flows.models import FlowPathCount

            from_uuids = flow.metadata["waiting_exit_uuids"]
            dates = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).aggregate(
                Max("period"), Min("period")
            )
            start_date = dates.get("period__min")
            end_date = dates.get("period__max")

            # by hour of the day
            hod = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra(
                {"hour": "extract(hour from period::timestamp)"}
            )
            hod = hod.values("hour").annotate(count=Sum("count")).order_by("hour")
            hod_dict = {int(h.get("hour")): h.get("count") for h in hod}

            hours = []
            for x in range(0, 24):
                hours.append({"bucket": datetime(1970, 1, 1, hour=x), "count": hod_dict.get(x, 0)})

            # by day of the week
            dow = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra(
                {"day": "extract(dow from period::timestamp)"}
            )
            dow = dow.values("day").annotate(count=Sum("count"))
            dow_dict = {int(d.get("day")): d.get("count") for d in dow}

            dow = []
            for x in range(0, 7):
                day_count = dow_dict.get(x, 0)
                dow.append({"day": x, "count": day_count})
                total_responses += day_count

            if total_responses > self.PERIOD_MIN:
                dow = sorted(dow, key=lambda k: k["day"])
                days = (
                    _("Sunday"),
                    _("Monday"),
                    _("Tuesday"),
                    _("Wednesday"),
                    _("Thursday"),
                    _("Friday"),
                    _("Saturday"),
                )
                dow = [
                    {
                        "day": days[d["day"]],
                        "count": d["count"],
                        "pct": 100 * float(d["count"]) / float(total_responses),
                    }
                    for d in dow
                ]
                context["dow"] = dow
                context["hod"] = hours

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

                histogram = histogram.values("bucket").annotate(count=Sum("count")).order_by("bucket")
                context["histogram"] = histogram

                # highcharts works in UTC, but we want to offset our chart according to the org timezone
                context["min_date"] = min_date

            stats = flow.get_run_stats()
            for status, count in stats["status"].items():
                context[status] = count

            context["total_runs"] = stats["total"]
            context["total_responses"] = total_responses

            return context

    class RunTable(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Intercooler helper which renders rows of runs to be embedded in an existing table with infinite scrolling
        """

        paginate_by = 50

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()
            runs = flow.runs.all()

            if str_to_bool(self.request.GET.get("responded", "true")):
                runs = runs.filter(responded=True)

            # paginate
            modified_on = self.request.GET.get("modified_on", None)
            if modified_on:
                id = self.request.GET["id"]

                modified_on = iso8601.parse_date(modified_on)
                runs = runs.filter(modified_on__lte=modified_on).exclude(id=id)

            # we grab one more than our page to denote whether there's more to get
            runs = list(runs.order_by("-modified_on")[: self.paginate_by + 1])
            context["more"] = len(runs) > self.paginate_by
            runs = runs[: self.paginate_by]

            result_fields = flow.metadata["results"]

            # populate result values
            for run in runs:
                results = run.results
                run.value_list = []
                for result_field in result_fields:
                    run.value_list.append(results.get(result_field["key"], None))

            context["runs"] = runs
            context["start_date"] = flow.org.get_delete_date(archive_type=Archive.TYPE_FLOWRUN)
            context["paginate_by"] = self.paginate_by
            return context

    class CategoryCounts(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse({"counts": self.get_object().get_category_counts()})

    class Results(SpaMixin, AllowOnlyActiveFlowMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("flows.flow_update"):
                menu.add_modax(
                    _("Download"),
                    "download-results",
                    f"{reverse('flows.flow_export_results')}?ids={obj.id}",
                    title=_("Download Results"),
                )

            if self.has_org_perm("flows.flow_editor"):
                menu.add_link(_("Edit Flow"), reverse("flows.flow_editor", args=[obj.uuid]))

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            flow = self.get_object()

            result_fields = []
            for result_field in flow.metadata[Flow.METADATA_RESULTS]:
                if not result_field["name"].startswith("_"):
                    result_field = result_field.copy()
                    result_field["has_categories"] = "true" if len(result_field["categories"]) > 1 else "false"
                    result_fields.append(result_field)
            context["result_fields"] = result_fields

            context["categories"] = flow.get_category_counts()
            context["utcoffset"] = int(datetime.now(flow.org.timezone).utcoffset().total_seconds() // 60)
            return context

    class Activity(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def get(self, request, *args, **kwargs):
            flow = self.get_object(self.get_queryset())
            (active, visited) = flow.get_activity()

            return JsonResponse(dict(nodes=active, segments=visited, is_starting=flow.is_starting()))

    class Simulate(OrgObjPermsMixin, SmartReadView):
        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        def get(self, request, *args, **kwargs):  # pragma: needs cover
            return HttpResponseRedirect(reverse("flows.flow_editor", args=[self.get_object().uuid]))

        def post(self, request, *args, **kwargs):
            try:
                json_dict = json.loads(request.body)
            except Exception as e:  # pragma: needs cover
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            if not settings.MAILROOM_URL:  # pragma: no cover
                return JsonResponse(
                    dict(status="error", description="mailroom not configured, cannot simulate"), status=500
                )

            flow = self.get_object()
            client = mailroom.get_client()

            analytics.track(request.user, "temba.flow_simulated", dict(flow=flow.name, uuid=flow.uuid))

            channel_uuid = "440099cf-200c-4d45-a8e7-4a564f4a0e8b"
            channel_name = "Test Channel"

            # build our request body, which includes any assets that mailroom should fake
            payload = {
                "org_id": flow.org_id,
                "assets": {
                    "channels": [
                        {
                            "uuid": channel_uuid,
                            "name": channel_name,
                            "address": "+18005551212",
                            "schemes": ["tel"],
                            "roles": ["send", "receive", "call"],
                            "country": "US",
                        }
                    ]
                },
            }

            if "flow" in json_dict:
                payload["flows"] = [{"uuid": flow.uuid, "definition": json_dict["flow"]}]

            # check if we are triggering a new session
            if "trigger" in json_dict:
                payload["trigger"] = json_dict["trigger"]

                # ivr flows need a connection in their trigger
                if flow.flow_type == Flow.TYPE_VOICE:
                    payload["trigger"]["connection"] = {
                        "channel": {"uuid": channel_uuid, "name": channel_name},
                        "urn": "tel:+12065551212",
                    }

                payload["trigger"]["environment"] = flow.org.as_environment_def()
                payload["trigger"]["user"] = self.request.user.as_engine_ref()

                try:
                    return JsonResponse(client.sim_start(payload))
                except mailroom.MailroomException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

            # otherwise we are resuming
            elif "resume" in json_dict:
                payload["resume"] = json_dict["resume"]
                payload["resume"]["environment"] = flow.org.as_environment_def()
                payload["session"] = json_dict["session"]

                try:
                    return JsonResponse(client.sim_resume(payload))
                except mailroom.MailroomException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

    class PreviewStart(OrgObjPermsMixin, SmartReadView):
        permission = "flows.flow_broadcast"

        blockers = {
            "already_starting": _(
                "This flow is already being started - please wait until that process completes before starting "
                "more contacts."
            ),
            "no_send_channel": _(
                'To start this flow you need to <a href="%(link)s">add a channel</a> to your workspace which will allow '
                "you to send messages to your contacts."
            ),
            "no_call_channel": _(
                'To start this flow you need to <a href="%(link)s">add a voice channel</a> to your workspace which will '
                "allow you to make and receive calls."
            ),
        }

        warnings = {
            "facebook_topic": _(
                "This flow does not specify a Facebook topic. You may still start this flow but Facebook contacts who "
                "have not sent an incoming message in the last 24 hours may not receive it."
            ),
            "no_templates": _(
                "This flow does not use message templates. You may still start this flow but WhatsApp contacts who "
                "have not sent an incoming message in the last 24 hours may not receive it."
            ),
            "inactive_threshold": _(
                "You've selected a lot of contacts! Depending on your channel "
                "it could take days to reach everybody and could reduce response rates. "
                "Click on <b>Skip inactive contacts</b> below "
                "to limit your selection to contacts who are more likely to respond."
            ),
        }

        def get_blockers(self, flow) -> list:
            blockers = []

            if flow.org.is_suspended:
                blockers.append(Org.BLOCKER_SUSPENDED)
            elif flow.org.is_flagged:
                blockers.append(Org.BLOCKER_FLAGGED)
            elif flow.is_starting():
                blockers.append(self.blockers["already_starting"])

            if flow.flow_type == Flow.TYPE_MESSAGE and not flow.org.get_send_channel():
                blockers.append(self.blockers["no_send_channel"] % {"link": reverse("channels.channel_claim")})
            elif flow.flow_type == Flow.TYPE_VOICE and not flow.org.get_call_channel():
                blockers.append(self.blockers["no_call_channel"] % {"link": reverse("channels.channel_claim")})

            return blockers

        def get_warnings(self, flow, query, total) -> list:

            warnings = []

            # if we are over our threshold, show the amount warning
            threshold = self.request.branding.get("inactive_threshold", 0)
            if "last_seen_on" not in query and threshold > 0 and total > threshold:
                warnings.append(self.warnings["inactive_threshold"])

            # facebook channels need to warn if no topic is set
            facebook_channel = flow.org.get_channel(Channel.ROLE_SEND, scheme=URN.FACEBOOK_SCHEME)
            if facebook_channel and not self.has_facebook_topic(flow):
                warnings.append(self.warnings["facebook_topic"])

            # if we have a whatsapp channel that requires a message template; exclude twilio whatsApp
            whatsapp_channel = flow.org.channels.filter(
                role__contains=Channel.ROLE_SEND, schemes__contains=[URN.WHATSAPP_SCHEME], is_active=True
            ).exclude(channel_type__in=["TWA"])
            if whatsapp_channel:
                # check to see we are using templates
                templates = flow.get_dependencies_metadata("template")
                if not templates:
                    warnings.append(self.warnings["no_templates"])

                # check that this template is synced and ready to go
                for ref in templates:
                    template = flow.org.templates.filter(uuid=ref["uuid"]).first()
                    if not template:
                        warnings.append(
                            _(f"The message template {ref['name']} does not exist on your account and cannot be sent.")
                        )
                    elif not template.is_approved():
                        warnings.append(
                            _(f"Your message template {template.name} is not approved and cannot be sent.")
                        )
            return warnings

        def has_facebook_topic(self, flow):
            if not flow.is_legacy():
                definition = flow.get_current_revision().get_migrated_definition()
                for node in definition.get("nodes", []):
                    for action in node.get("actions", []):
                        if action.get("type", "") == "send_msg" and action.get("topic", ""):
                            return True

        def post(self, request, *args, **kwargs):
            payload = json.loads(request.body)
            include = mailroom.QueryInclusions(**payload.get("include", {}))
            exclude = mailroom.QueryExclusions(**payload.get("exclude", {}))
            flow = self.get_object()
            org = flow.org

            try:
                query, total, sample, metadata = flow.preview_start(include=include, exclude=exclude)
            except SearchException as e:
                return JsonResponse({"query": "", "total": 0, "sample": [], "error": str(e)}, status=400)

            query_fields = org.fields.filter(key__in=[f["key"] for f in metadata.fields])

            # render sample contacts in a simplified form, including only fields from query
            contacts = []
            for contact in sample:
                primary_urn = contact.get_urn()
                primary_urn = primary_urn.get_display(org, international=True) if primary_urn else None
                contacts.append(
                    {
                        "uuid": contact.uuid,
                        "name": contact.name,
                        "primary_urn": primary_urn,
                        "fields": {f.key: contact.get_field_display(f) for f in query_fields},
                        "created_on": contact.created_on.isoformat(),
                        "last_seen_on": contact.last_seen_on.isoformat() if contact.last_seen_on else None,
                    }
                )

            return JsonResponse(
                {
                    "query": query,
                    "total": total,
                    "sample": contacts,
                    "fields": [{"key": f.key, "name": f.name} for f in query_fields],
                    "warnings": self.get_warnings(flow, query, total),
                    "blockers": self.get_blockers(flow),
                }
            )

    class Broadcast(OrgPermsMixin, ModalMixin):
        class Form(forms.ModelForm):

            flow = TembaChoiceField(
                queryset=Flow.objects.none(),
                required=True,
                widget=SelectWidget(
                    attrs={"placeholder": _("Select a flow to start"), "widget_only": True, "searchable": True}
                ),
            )

            recipients = OmniboxField(
                label=_("Recipients"),
                required=False,
                help_text=_("The contacts to send the message to"),
                widget=OmniboxChoice(
                    attrs={
                        "placeholder": _("Recipients, enter contacts or groups"),
                        "widget_only": True,
                        "groups": True,
                        "contacts": True,
                        "urns": True,
                    }
                ),
            )

            query = forms.CharField(
                required=False,
                widget=ContactSearchWidget(attrs={"widget_only": True, "placeholder": _("Enter contact query")}),
            )

            def __init__(self, org, **kwargs):
                super().__init__(**kwargs)
                self.org = org

                self.fields["flow"].queryset = org.flows.filter(
                    flow_type__in=(Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND),
                    is_archived=False,
                    is_system=False,
                    is_active=True,
                ).order_by("name")

            def clean_flow(self):
                flow = self.cleaned_data.get("flow")

                # these should be caught as part of StartPreview
                assert not flow.org.is_suspended and not flow.org.is_flagged and not flow.is_starting()

                return flow

            def clean_query(self):
                query = self.cleaned_data.get("query")
                if query:
                    try:
                        parsed = parse_query(self.org, query)
                        query = parsed.query
                    except SearchException as e:
                        raise ValidationError(str(e))

                return query

            def clean(self):
                cleaned_data = super().clean()

                if self.is_valid():
                    query = cleaned_data.get("query")

                    if not query:
                        self.add_error("query", _("This field is required."))

                return cleaned_data

            class Meta:
                model = Flow
                fields = ("query",)

        form_class = Form
        submit_button_name = _("Start Flow")
        success_message = ""
        success_url = "hide"

        def derive_initial(self):
            org = self.request.org
            contacts = self.request.GET.get("c", "")
            contacts = org.contacts.filter(uuid__in=contacts.split(","))
            recipients = []
            for contact in contacts:
                urn = contact.get_urn()
                if urn:
                    urn = urn.get_display(org=org, international=True)
                recipients.append({"id": contact.uuid, "name": contact.name, "urn": urn, "type": "contact"})

            initial = {"recipients": recipients}
            flow_id = self.request.GET.get("flow", None)
            if flow_id:
                initial["flow"] = flow_id

            return initial

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["flow"] = self.request.GET.get("flow", None)
            return context

        def form_valid(self, form):
            query = form.cleaned_data["query"]
            flow = form.cleaned_data["flow"]
            analytics.track(self.request.user, "temba.flow_broadcast", dict(query=query))

            # queue the flow start to be started by mailroom
            flow.async_start(
                self.request.user,
                groups=(),
                contacts=(),
                query=query,
                restart_participants=True,
                include_active=True,
            )
            return super().form_valid(form)

    class Assets(OrgPermsMixin, SmartTemplateView):
        """
        Provides environment and languages to the new editor
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<org>\d+)/(?P<fingerprint>[\w-]+)/(?P<type>environment|language)/((?P<uuid>[a-z0-9-]{{36}})/)?$"

        def derive_org(self):
            if not hasattr(self, "org"):
                self.org = Org.objects.get(id=self.kwargs["org"])
            return self.org

        def get(self, *args, **kwargs):
            org = self.derive_org()
            asset_type_name = kwargs["type"]

            if asset_type_name == "environment":
                return JsonResponse(org.as_environment_def())
            else:
                results = [{"iso": code, "name": languages.get_name(code)} for code in org.flow_languages]
                return JsonResponse({"results": sorted(results, key=lambda lang: lang["name"])})


# this is just for adhoc testing of the preprocess url
class PreprocessTest(FormView):  # pragma: no cover
    @csrf_exempt
    def dispatch(self, *args, **kwargs):
        return super().dispatch(*args, **kwargs)

    def post(self, request, *args, **kwargs):
        return HttpResponse(
            json.dumps(dict(text="Norbert", extra=dict(occupation="hoopster", skillz=7.9))),
            content_type="application/json",
        )


class FlowLabelForm(forms.ModelForm):
    name = forms.CharField(required=True, widget=InputWidget(), label=_("Name"))
    flows = forms.CharField(required=False, widget=forms.HiddenInput)

    def __init__(self, org, *args, **kwargs):
        self.org = org

        super().__init__(*args, **kwargs)

    def clean_name(self):
        name = self.cleaned_data["name"].strip()
        if self.org.flow_labels.filter(name=name).exclude(id=self.instance.id).exists():
            raise ValidationError(_("Must be unique."))
        return name

    class Meta:
        model = FlowLabel
        fields = ("name",)


class FlowLabelCRUDL(SmartCRUDL):
    model = FlowLabel
    actions = ("create", "update", "delete")

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("uuid",)
        redirect_url = "@flows.flow_list"
        cancel_url = "@flows.flow_list"
        success_message = ""
        submit_button_name = _("Delete")

        def get_success_url(self):
            return reverse("flows.flow_list")

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.delete()
            return self.render_modal_response()

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = FlowLabelForm
        success_url = "uuid@flows.flow_filter"
        success_message = ""

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "flows")
        success_url = "hide"
        form_class = FlowLabelForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def save(self, obj):
            self.object = FlowLabel.create(self.request.org, self.request.user, obj.name)

        def post_save(self, obj, *args, **kwargs):
            obj = super().post_save(obj, *args, **kwargs)

            flow_ids = []
            if self.form.cleaned_data["flows"]:  # pragma: needs cover
                flow_ids = [int(f) for f in self.form.cleaned_data["flows"].split(",") if f.isdigit()]

            flows = obj.org.flows.filter(is_active=True, id__in=flow_ids)
            if flows:  # pragma: needs cover
                obj.toggle_label(flows, add=True)

            return obj


class FlowStartCRUDL(SmartCRUDL):
    model = FlowStart
    actions = ("list",)

    class List(SpaMixin, OrgFilterMixin, OrgPermsMixin, ContentMenuMixin, SmartListView):
        title = _("Flow Start Log")
        ordering = ("-created_on",)
        select_related = ("flow", "created_by")
        paginate_by = 25

        def build_content_menu(self, menu):
            menu.add_link(_("Flows"), reverse("flows.flow_list"))

        def derive_queryset(self, *args, **kwargs):
            qs = super().derive_queryset(*args, **kwargs)

            if self.request.GET.get("type") == "manual":
                qs = qs.filter(start_type=FlowStart.TYPE_MANUAL)
            else:
                qs = qs.filter(start_type__in=(FlowStart.TYPE_MANUAL, FlowStart.TYPE_API, FlowStart.TYPE_API_ZAPIER))

            return qs.prefetch_related("contacts", "groups")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)

            filtered = False
            if self.request.GET.get("type") == "manual":
                context["url_params"] = "?type=manual&"
                filtered = True

            context["filtered"] = filtered

            FlowStartCount.bulk_annotate(context["object_list"])

            return context
