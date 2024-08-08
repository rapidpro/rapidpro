import logging
from datetime import datetime, timedelta
from urllib.parse import urlencode

import regex
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
from django.contrib.humanize.templatetags import humanize
from django.core.exceptions import ValidationError
from django.db.models import Count, Max, Min, Sum
from django.db.models.functions import Lower
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils.encoding import force_str
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _, ngettext_lazy as _p
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import FormView

from temba import mailroom
from temba.channels.models import Channel
from temba.contacts.models import URN
from temba.flows.models import Flow, FlowRevision, FlowRun, FlowSession, FlowStart
from temba.flows.tasks import update_session_wait_expires
from temba.ivr.models import Call
from temba.orgs.models import IntegrationType, Org
from temba.orgs.views import (
    BaseExportView,
    DependencyDeleteModal,
    MenuMixin,
    ModalMixin,
    OrgFilterMixin,
    OrgObjPermsMixin,
    OrgPermsMixin,
)
from temba.triggers.models import Trigger
from temba.utils import analytics, gettext, json, languages, on_transaction_commit
from temba.utils.fields import (
    CheckboxWidget,
    ContactSearchWidget,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
)
from temba.utils.text import slugify_with
from temba.utils.views import BulkActionMixin, ContentMenuMixin, SpaMixin, StaffOnlyMixin

from .models import FlowLabel, FlowStartCount, FlowUserConflictException, FlowVersionConflictException, ResultsExport

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
            conflicts = Trigger.get_conflicts(self.org, Trigger.TYPE_KEYWORD, keywords=[keyword])
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

        return cleaned_keywords

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
                session_id=session.id,
                org=session.org.name,
                org_id=session.org_id,
                site=f"https://{session.org.get_brand_domain()}",
            )
            return JsonResponse(output, json_dumps_params=dict(indent=2))


class FlowRunCRUDL(SmartCRUDL):
    actions = ("delete",)
    model = FlowRun

    class Delete(ModalMixin, OrgObjPermsMixin, SmartDeleteView):
        fields = ("id",)
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
        "category_counts",
        "preview_start",
        "start",
        "activity",
        "activity_chart",
        "activity_data",
        "filter",
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
            labels = FlowLabel.objects.filter(org=self.request.org).order_by(Lower("name"))

            menu = []
            menu.append(self.create_menu_item(menu_id="", name=_("Active"), icon="active", href="flows.flow_list"))
            menu.append(
                self.create_menu_item(
                    name=_("Archived"),
                    icon="archive",
                    href="flows.flow_archived",
                    perm="flows.flow_list",
                )
            )

            if self.has_org_perm("globals.global_list"):
                menu.append(self.create_divider()),
                menu.append(self.create_menu_item(name=_("Globals"), icon="global", href="globals.global_list"))

            label_items = []
            for label in labels:
                label_items.append(
                    self.create_menu_item(
                        icon="label",
                        menu_id=label.uuid,
                        name=label.name,
                        href=reverse("flows.flow_filter", args=[label.uuid]),
                        perm="flows.flow_list",
                        count=label.get_flow_count(),
                    )
                )

            history_items = []
            if self.has_org_perm("request_logs.httplog_webhooks"):
                history_items.append(
                    self.create_menu_item(
                        menu_id="webhooks", name=_("Webhooks"), href=reverse("request_logs.httplog_webhooks")
                    )
                )

            if self.has_org_perm("flows.flowstart_list"):
                history_items.append(
                    self.create_menu_item(menu_id="starts", name=_("Flow Starts"), href=reverse("flows.flowstart_list"))
                )

            if history_items:
                menu.append(
                    self.create_menu_item(
                        name=_("History"),
                        items=history_items,
                        inline=True,
                    )
                )

            if label_items:
                menu.append(self.create_menu_item(name=_("Labels"), items=label_items, inline=True))

            return menu

    class RecentContacts(OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for the rollover of recent contacts coming out of a split
        """

        permission = "flows.flow_editor"
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

        permission = "flows.flow_editor"  # POSTs explicitly check for flows.flow_update
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
                flow_info = mailroom.get_client().flow_inspect(flow.org, definition)
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

            except mailroom.FlowValidationException as e:
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
                label=_("Keyword triggers"),
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

            def __init__(self, org, branding, *args, **kwargs):
                super().__init__(org, branding, *args, **kwargs)

                self.fields["flow_type"] = forms.ChoiceField(
                    label=_("Type"),
                    help_text=_("Choose the method for your flow"),
                    choices=Flow.TYPE_CHOICES[:3],  # exclude SURVEY from options
                    widget=SelectWidget(
                        attrs={"widget_only": False},
                        option_attrs={
                            Flow.TYPE_BACKGROUND: {"icon": "flow_background"},
                            Flow.TYPE_SURVEY: {"icon": "flow_surveyor"},
                            Flow.TYPE_VOICE: {"icon": "flow_ivr"},
                        },
                    ),
                )

                self.fields["base_language"] = forms.ChoiceField(
                    label=_("Language"),
                    initial=org.flow_languages[0],
                    choices=languages.choices(org.flow_languages),
                    widget=SelectWidget(attrs={"widget_only": False}),
                )

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "flow_type", "base_language")
                widgets = {"name": InputWidget()}

        form_class = Form
        success_url = "uuid@flows.flow_editor"
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

            # create a triggers if user provided keywords
            keywords = self.form.cleaned_data["keyword_triggers"]
            if keywords:
                Trigger.create(
                    org,
                    user,
                    Trigger.TYPE_KEYWORD,
                    flow=obj,
                    keywords=keywords,
                    match_type=Trigger.MATCH_FIRST_WORD,
                )

            return obj

    class Delete(DependencyDeleteModal):
        cancel_url = "uuid@flows.flow_editor"
        success_url = "@flows.flow_list"

    class Copy(OrgObjPermsMixin, SmartUpdateView):
        fields = []

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

        class BaseOnlineForm(BaseFlowForm):
            keyword_triggers = forms.CharField(
                required=False,
                label=_("Keyword triggers"),
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
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_VOICE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_VOICE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                existing_keywords = set()
                for trigger in self.instance.triggers.filter(is_archived=False, trigger_type=Trigger.TYPE_KEYWORD):
                    existing_keywords.update(trigger.keywords)

                self.fields["keyword_triggers"].initial = list(sorted(existing_keywords))

        class VoiceForm(BaseOnlineForm):
            ivr_retry = forms.ChoiceField(
                label=_("Retry call if unable to connect"),
                help_text=_("Retries call three times for the chosen interval"),
                initial=60,
                choices=Call.RETRY_CHOICES,
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["ivr_retry"].initial = self.instance.metadata.get("ivr_retry", 60)

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers", "ivr_retry")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

        class MessagingForm(BaseOnlineForm):
            expires_after_minutes = forms.ChoiceField(
                label=_("Expire inactive contacts"),
                help_text=_("When inactive contacts should be removed from the flow"),
                initial=Flow.EXPIRES_DEFAULTS[Flow.TYPE_MESSAGE],
                choices=Flow.EXPIRES_CHOICES[Flow.TYPE_MESSAGE],
                widget=SelectWidget(attrs={"widget_only": False}),
            )

            class Meta:
                model = Flow
                fields = ("name", "keyword_triggers", "expires_after_minutes", "ignore_triggers")
                widgets = {"name": InputWidget(), "ignore_triggers": CheckboxWidget()}

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
            keyword_triggers = self.form.cleaned_data.get("keyword_triggers")

            if keyword_triggers is not None:
                self.update_triggers(obj, self.request.user, keyword_triggers)

            on_transaction_commit(lambda: update_session_wait_expires.delay(obj.id))

            return obj

        def update_triggers(self, flow, user, new_keywords: list):
            existing_keywords = set()

            # update existing keyword triggers for this flow, archiving any that are no longer valid
            for trigger in flow.triggers.filter(trigger_type=Trigger.TYPE_KEYWORD, is_archived=False, is_active=True):
                if set(trigger.keywords).issubset(new_keywords):
                    existing_keywords.update(trigger.keywords)
                else:
                    trigger.archive(user)

            missing_keywords = [k for k in new_keywords if k not in existing_keywords]

            if missing_keywords:
                # look for archived trigger, with default empty settings, whose keywords match, that we can restore
                archived = flow.triggers.filter(
                    trigger_type=Trigger.TYPE_KEYWORD,
                    keywords__contains=missing_keywords,
                    keywords__contained_by=new_keywords,
                    channel=None,
                    groups=None,
                    exclude_groups=None,
                    is_archived=True,
                    is_active=True,
                ).first()

                if archived:
                    archived.restore(user)
                else:
                    Trigger.create(
                        flow.org,
                        user,
                        Trigger.TYPE_KEYWORD,
                        flow,
                        keywords=missing_keywords,
                        match_type=Trigger.MATCH_FIRST_WORD,
                    )

    class BaseList(SpaMixin, OrgFilterMixin, OrgPermsMixin, BulkActionMixin, ContentMenuMixin, SmartListView):
        permission = "flows.flow_list"
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

            if self.has_org_perm("orgs.orgimport_create"):
                menu.add_link(_("Import"), reverse("orgs.orgimport_create"))
            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export"), reverse("orgs.org_export"))

    class Archived(BaseList):
        title = _("Archived")
        bulk_actions = ("restore",)
        default_order = ("-created_on",)

        def derive_queryset(self, *args, **kwargs):
            return super().derive_queryset(*args, **kwargs).filter(is_active=True, is_archived=True)

    class List(BaseList):
        title = _("Active")
        bulk_actions = ("archive", "label", "export-results")
        menu_path = "/flow/active"

        def derive_queryset(self, *args, **kwargs):
            queryset = super().derive_queryset(*args, **kwargs)
            queryset = queryset.filter(is_active=True, is_archived=False)
            return queryset

    class Filter(BaseList, OrgObjPermsMixin):
        add_button = True
        bulk_actions = ("label", "export-results")
        slug_url_kwarg = "uuid"

        def derive_menu_path(self):
            return f"/flow/labels/{self.label.uuid}"

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
                    _("Delete"),
                    "delete-label",
                    f"{reverse('flows.flowlabel_delete', args=[self.label.id])}",
                    title=_("Delete Label"),
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

        def derive_menu_path(self):
            if self.object.is_archived:
                return "/flow/archived"
            return "/flow/active"

        def derive_title(self):
            return self.object.name

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            context["migrate"] = "migrate" in self.request.GET

            flow = self.object

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
                features.append("optins")
            if whatsapp_channel:
                features.append("whatsapp")
            if org.get_integrations(IntegrationType.Category.AIRTIME):
                features.append("airtime")
            if org.classifiers.filter(is_active=True).exists():
                features.append("classifier")
            if org.get_resthooks():
                features.append("resthook")
            if org.country_id:
                features.append("locations")

            return features

        def build_content_menu(self, menu):
            obj = self.get_object()

            if obj.flow_type != Flow.TYPE_SURVEY and self.has_org_perm("flows.flow_start") and not obj.is_archived:
                menu.add_modax(
                    _("Start"),
                    "start-flow",
                    f"{reverse('flows.flow_start', args=[])}?flow={obj.id}",
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
                menu.add_modax(
                    _("Export Translation"),
                    "export-translation",
                    reverse("flows.flow_export_translation", args=[obj.id]),
                )

                if self.has_org_perm("flows.flow_update"):
                    menu.add_link(_("Import Translation"), reverse("flows.flow_import_translation", args=[obj.id]))

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

        permission = "flows.flow_update"
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

        permission = "flows.flow_editor"
        form_class = Form
        submit_button_name = _("Export")
        success_url = "@flows.flow_list"

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_success_url(self):
            params = {"flow": self.object.id, "language": self.form.cleaned_data["language"]}
            return reverse("flows.flow_download_translation") + "?" + urlencode(params, doseq=True)

        def form_valid(self, form):
            return self.render_modal_response(form)

    class DownloadTranslation(OrgPermsMixin, SmartListView):
        """
        Download link for PO translation files extracted from flows by mailroom
        """

        permission = "flows.flow_editor"

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

    class ImportTranslation(SpaMixin, OrgObjPermsMixin, SmartUpdateView):
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
                if instance.base_language in lang_codes:
                    lang_codes.remove(instance.base_language)

                self.fields["language"].choices = languages.choices(codes=lang_codes)

        permission = "flows.flow_update"
        title = _("Import Translation")
        submit_button_name = _("Import")
        success_url = "uuid@flows.flow_editor"
        menu_path = "/flow/active"

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
                Flow.objects.none(), required=True, widget=forms.MultipleHiddenInput()
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

        permission = "flows.flow_results"
        form_class = Form
        export_type = ResultsExport
        success_url = "@flows.flow_list"

        def derive_initial(self):
            initial = super().derive_initial()

            flow_ids = self.request.GET.get("ids")
            if flow_ids:
                initial["flows"] = self.request.org.flows.filter(is_active=True, id__in=flow_ids.split(","))

            return initial

        def derive_exclude(self):
            return ["extra_urns"] if self.request.org.is_anon else []

        def create_export(self, org, user, form):
            return ResultsExport.create(
                org,
                user,
                start_date=form.cleaned_data["start_date"],
                end_date=form.cleaned_data["end_date"],
                flows=form.cleaned_data["flows"],
                with_fields=form.cleaned_data["with_fields"],
                with_groups=form.cleaned_data["with_groups"],
                responded_only=form.cleaned_data["responded_only"],
                extra_urns=form.cleaned_data.get("extra_urns", []),
            )

    class ActivityData(OrgObjPermsMixin, SmartReadView):
        # the min number of responses to show a histogram
        HISTOGRAM_MIN = 0

        # the min number of responses to show the period charts
        PERIOD_MIN = 0

        permission = "flows.flow_results"

        def render_to_response(self, context, **response_kwargs):
            total_responses = 0
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
                hours.append([x, hod_dict.get(x, 0)])

            # by day of the week
            dow = FlowPathCount.objects.filter(flow=flow, from_uuid__in=from_uuids).extra(
                {"day": "extract(dow from period::timestamp)"}
            )
            dow = dow.values("day").annotate(count=Sum("count"))
            dow_dict = {int(d.get("day")): d.get("count") for d in dow}

            dow = []
            for x in range(0, 7):
                day_count = dow_dict.get(x, 0)
                dow.append({"name": x, "msgs": day_count})
                total_responses += day_count

            if total_responses > self.PERIOD_MIN:
                dow = sorted(dow, key=lambda k: k["name"])
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
                        "name": days[d["name"]],
                        "msgs": d["msgs"],
                        "y": 100 * float(d["msgs"]) / float(total_responses),
                    }
                    for d in dow
                ]

            min_date = None
            histogram = []

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
                histogram = [[_["bucket"], _["count"]] for _ in histogram]

            summary = {
                "responses": total_responses,
            }

            stats = flow.get_run_stats()
            for status, count in stats["status"].items():
                summary[status] = count

            completion = {
                "summary": [
                    {
                        "name": _("Active"),
                        "y": summary.get("active", 0) + summary.get("waiting", 0),
                        "drilldown": None,
                        "color": "#2387CA",
                    },
                    {"name": _("Completed"), "y": summary.get("completed", 0), "drilldown": None, "color": "#8FC93A"},
                    {
                        "name": _("Interrupted, Expired and Failed"),
                        "y": summary.get("interrupted", 0) + summary.get("expired", 0) + summary.get("failed", 0),
                        "drilldown": "incomplete",
                        "color": "#CCC",
                    },
                ],
                "drilldown": [
                    {
                        "name": "Interrupted, Expired and Failed",
                        "id": "incomplete",
                        "innerSize": "50%",
                        "data": [
                            {"name": _("Expired"), "y": summary.get("expired", 0), "color": "#CCC"},
                            {"name": _("Interrupted"), "y": summary.get("interrupted", 0), "color": "#EEE"},
                            {"name": _("Failed"), "y": summary.get("failed", 0), "color": "#FEE"},
                        ],
                    }
                ],
            }

            summary["title"] = _p("%(total)s Response", "%(total)s Responses", summary["responses"]) % {
                "total": humanize.intcomma(summary["responses"])
            }

            return JsonResponse(
                {
                    "start_date": start_date,
                    "end_date": end_date,
                    "min_date": min_date,
                    "summary": summary,
                    "dow": dow,
                    "hod": hours,
                    "histogram": histogram,
                    "completion": completion,
                },
                json_dumps_params={"indent": 2},
                encoder=json.EpochEncoder,
            )

    class ActivityChart(SpaMixin, AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        permission = "flows.flow_results"

    class CategoryCounts(AllowOnlyActiveFlowMixin, OrgObjPermsMixin, SmartReadView):
        """
        Used by the editor for the counts on split exits
        """

        permission = "flows.flow_editor"
        slug_url_kwarg = "uuid"

        def render_to_response(self, context, **response_kwargs):
            return JsonResponse({"counts": self.get_object().get_category_counts()})

    class Results(SpaMixin, AllowOnlyActiveFlowMixin, OrgObjPermsMixin, ContentMenuMixin, SmartReadView):
        slug_url_kwarg = "uuid"

        def build_content_menu(self, menu):
            obj = self.get_object()

            if self.has_org_perm("flows.flow_results"):
                menu.add_modax(
                    _("Export"),
                    "export-results",
                    f"{reverse('flows.flow_export_results')}?ids={obj.id}",
                    title=_("Export Results"),
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
        """
        Used by the editor for the counts on paths between nodes
        """

        permission = "flows.flow_editor"
        slug_url_kwarg = "uuid"

        def get(self, request, *args, **kwargs):
            flow = self.get_object(self.get_queryset())
            (active, visited) = flow.get_activity()

            return JsonResponse(dict(nodes=active, segments=visited, is_starting=flow.is_starting()))

    class Simulate(OrgObjPermsMixin, SmartReadView):
        permission = "flows.flow_editor"

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

                # ivr flows need a call in their trigger
                if flow.flow_type == Flow.TYPE_VOICE:
                    payload["trigger"]["call"] = {
                        "channel": {"uuid": channel_uuid, "name": channel_name},
                        "urn": "tel:+12065551212",
                    }

                payload["trigger"]["environment"] = flow.org.as_environment_def()
                payload["trigger"]["user"] = self.request.user.as_engine_ref()

                try:
                    return JsonResponse(client.sim_start(payload))
                except mailroom.RequestException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

            # otherwise we are resuming
            elif "resume" in json_dict:
                payload["resume"] = json_dict["resume"]
                payload["resume"]["environment"] = flow.org.as_environment_def()
                payload["session"] = json_dict["session"]

                try:
                    return JsonResponse(client.sim_resume(payload))
                except mailroom.RequestException:
                    return JsonResponse(dict(status="error", description="mailroom error"), status=500)

    class PreviewStart(OrgObjPermsMixin, SmartReadView):
        permission = "flows.flow_start"

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
            "no_templates": _(
                "This flow does not use message templates. You may still start this flow but WhatsApp contacts who "
                "have not sent an incoming message in the last 24 hours may not receive it."
            ),
            "inactive_threshold": _(
                "You've selected a lot of contacts! Depending on your channel "
                "it could take days to reach everybody and could reduce response rates. "
                "Filter for contacts that have sent a message recently "
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
                        warnings.append(_(f"Your message template {template.name} is not approved and cannot be sent."))
            return warnings

        def post(self, request, *args, **kwargs):
            payload = json.loads(request.body)
            include = mailroom.Inclusions(**payload.get("include", {}))
            exclude = mailroom.Exclusions(**payload.get("exclude", {}))
            flow = self.get_object()

            try:
                query, total = FlowStart.preview(flow, include=include, exclude=exclude)
            except mailroom.QueryValidationException as e:
                return JsonResponse({"query": "", "total": 0, "error": str(e)}, status=400)

            return JsonResponse(
                {
                    "query": query,
                    "total": total,
                    "warnings": self.get_warnings(flow, query, total),
                    "blockers": self.get_blockers(flow),
                }
            )

    class Start(OrgPermsMixin, ModalMixin):
        class Form(forms.ModelForm):
            flow = TembaChoiceField(
                queryset=Flow.objects.none(),
                required=True,
                widget=SelectWidget(
                    attrs={"placeholder": _("Select a flow to start"), "widget_only": True, "searchable": True}
                ),
            )

            contact_search = forms.JSONField(
                required=True,
                widget=ContactSearchWidget(attrs={"widget_only": True, "placeholder": _("Enter contact query")}),
            )

            def __init__(self, org, flow, **kwargs):
                super().__init__(**kwargs)
                self.org = org

                self.fields["flow"].queryset = org.flows.filter(
                    flow_type__in=(Flow.TYPE_MESSAGE, Flow.TYPE_VOICE, Flow.TYPE_BACKGROUND),
                    is_archived=False,
                    is_system=False,
                    is_active=True,
                ).order_by(Lower("name"))

                if flow:
                    self.fields["flow"].widget = forms.HiddenInput(
                        attrs={"placeholder": _("Select a flow to start"), "widget_only": True, "searchable": True}
                    )

                    search_attrs = self.fields["contact_search"].widget.attrs
                    search_attrs["endpoint"] = reverse("flows.flow_preview_start", args=[flow.id])
                    search_attrs["started_previously"] = True
                    search_attrs["not_seen_since_days"] = True
                    if flow.flow_type != Flow.TYPE_BACKGROUND:
                        search_attrs["in_a_flow"] = True

            def clean_contact_search(self):
                contact_search = self.cleaned_data.get("contact_search")
                recipients = contact_search.get("recipients", [])

                if contact_search["advanced"] and ("query" not in contact_search or not contact_search["query"]):
                    raise ValidationError(_("A contact query is required."))

                if not contact_search["advanced"] and len(recipients) == 0:
                    raise ValidationError(_("Contacts or groups are required."))

                if contact_search["advanced"]:
                    try:
                        contact_search["parsed_query"] = (
                            mailroom.get_client()
                            .contact_parse_query(self.org, contact_search["query"], parse_only=True)
                            .query
                        )
                    except mailroom.QueryValidationException as e:
                        raise ValidationError(str(e))

                return contact_search

            class Meta:
                model = Flow
                fields = ("flow", "contact_search")

        form_class = Form
        submit_button_name = _("Start")
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

            return {
                "contact_search": {"recipients": recipients, "advanced": False, "query": "", "exclusions": {}},
                "flow": self.flow.id if self.flow else None,
            }

        @cached_property
        def flow(self) -> Flow:
            flow_id = self.request.GET.get("flow", None)
            return self.request.org.flows.filter(id=flow_id, is_active=True).first() if flow_id else None

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["flow"] = self.flow
            return kwargs

        def form_valid(self, form):
            contact_search = form.cleaned_data["contact_search"]
            flow = form.cleaned_data["flow"]
            analytics.track(self.request.user, "temba.flow_start", contact_search)

            recipients = contact_search.get("recipients", [])
            groups, contacts = ContactSearchWidget.parse_recipients(self.request.org, recipients)

            # queue the flow start to be started by mailroom
            flow.async_start(
                self.request.user,
                groups=groups,
                contacts=contacts,
                query=contact_search["parsed_query"] if "parsed_query" in contact_search else None,
                exclusions=contact_search.get("exclusions", {}),
            )
            return super().form_valid(form)

    class Assets(OrgPermsMixin, SmartTemplateView):
        """
        TODO update editor to use API endpoint instead of this
        """

        @classmethod
        def derive_url_pattern(cls, path, action):
            return rf"^{path}/{action}/(?P<org>\d+)/(?P<fingerprint>[\w-]+)/(?P<type>language)/((?P<uuid>[a-z0-9-]{{36}})/)?$"

        def derive_org(self):
            if not hasattr(self, "org"):
                self.org = Org.objects.get(id=self.kwargs["org"])
            return self.org

        def get(self, *args, **kwargs):
            org = self.derive_org()

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
        success_url = "@flows.flow_list"
        cancel_url = "@flows.flow_list"
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

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        fields = ("name", "flows")
        form_class = FlowLabelForm
        submit_button_name = _("Create")

        def get_success_url(self):
            return reverse("flows.flow_filter", args=[self.object.uuid])

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

    class List(SpaMixin, OrgFilterMixin, OrgPermsMixin, SmartListView):
        title = _("Flow Starts")
        ordering = ("-created_on",)
        select_related = ("flow", "created_by")
        paginate_by = 25
        menu_path = "/flow/history/starts"

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
