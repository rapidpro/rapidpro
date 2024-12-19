from datetime import timedelta

from smartmin.views import (
    SmartCreateView,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.contrib import messages
from django.db.models.functions import Lower
from django.http import HttpResponse, HttpResponseRedirect, JsonResponse
from django.urls import reverse
from django.utils import timezone
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _

from temba.contacts.models import ContactField, ContactGroup
from temba.utils import on_transaction_commit
from temba.utils.fields import SelectMultipleWidget, TembaDateField
from temba.utils.views.mixins import ComponentFormMixin, ModalFormMixin

from .mixins import DependencyMixin, OrgObjPermsMixin, OrgPermsMixin


class BaseReadView(OrgObjPermsMixin, SmartReadView):
    """
    Base detail view for an object that belong to the current org
    """

    def derive_queryset(self, **kwargs):
        qs = super().derive_queryset(**kwargs)

        # filter by allowed org as we'll let OrgObjPermsMixin provide a redirect
        if not self.request.user.is_staff:
            qs = qs.filter(org__in=self.request.user.orgs.all())

        if hasattr(self.model, "is_active"):
            qs = qs.filter(is_active=True)

        return qs.select_related("org")


class BaseCreateModal(ComponentFormMixin, ModalFormMixin, OrgPermsMixin, SmartCreateView):
    """
    Base create modal view
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.request.org
        return kwargs

    def pre_save(self, obj):
        obj = super().pre_save(obj)
        obj.org = self.request.org
        return obj


class BaseUpdateModal(ComponentFormMixin, ModalFormMixin, OrgObjPermsMixin, SmartUpdateView):
    """
    Base update modal view
    """

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.request.org
        return kwargs

    def derive_queryset(self, **kwargs):
        qs = super().derive_queryset(**kwargs).filter(org=self.request.org)

        if hasattr(self.model, "is_active"):
            qs = qs.filter(is_active=True)

        if hasattr(self.model, "is_system"):
            qs = qs.filter(is_system=False)

        return qs


class BaseDeleteModal(OrgObjPermsMixin, SmartDeleteView):
    fields = ("id",)
    submit_button_name = _("Delete")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["submit_button_name"] = self.submit_button_name
        return context

    def post(self, request, *args, **kwargs):
        obj = self.get_object()

        if not getattr(obj, "is_system", False):
            obj.release(self.request.user)

        return HttpResponseRedirect(self.get_redirect_url())


class BaseListView(OrgPermsMixin, SmartListView):
    """
    Base list view for objects that belong to the current org
    """

    def derive_queryset(self, **kwargs):
        qs = super().derive_queryset(**kwargs).filter(org=self.request.org)

        if hasattr(self.model, "is_active"):
            qs = qs.filter(is_active=True)

        return qs


class BaseMenuView(OrgPermsMixin, SmartTemplateView):
    """
    Base view for the section menus
    """

    def create_divider(self):
        return {"type": "divider"}

    def create_space(self):  # pragma: no cover
        return {"type": "space"}

    def create_section(self, name, items=()):  # pragma: no cover
        return {"id": slugify(name), "name": name, "type": "section", "items": items}

    def create_list(self, name, href, type):
        return {"id": name, "href": href, "type": type}

    def create_modax_button(self, name, href, icon=None, on_submit=None):  # pragma: no cover
        menu_item = {"id": slugify(name), "name": name, "type": "modax-button"}
        if href:
            if href[0] == "/":  # pragma: no cover
                menu_item["href"] = href
            elif self.has_org_perm(href):
                menu_item["href"] = reverse(href)

        if on_submit:
            menu_item["on_submit"] = on_submit

        if icon:  # pragma: no cover
            menu_item["icon"] = icon

        if "href" not in menu_item:  # pragma: no cover
            return None

        return menu_item

    def create_menu_item(
        self,
        menu_id=None,
        name=None,
        icon=None,
        avatar=None,
        endpoint=None,
        href=None,
        count=None,
        perm=None,
        items=[],
        inline=False,
        bottom=False,
        popup=False,
        event=None,
        posterize=False,
        bubble=None,
        mobile=False,
    ):
        if perm and not self.has_org_perm(perm):  # pragma: no cover
            return

        menu_item = {"name": name, "inline": inline}
        menu_item["id"] = menu_id if menu_id else slugify(name)
        menu_item["bottom"] = bottom
        menu_item["popup"] = popup
        menu_item["avatar"] = avatar
        menu_item["posterize"] = posterize
        menu_item["event"] = event
        menu_item["mobile"] = mobile

        if bubble:
            menu_item["bubble"] = bubble

        if icon:
            menu_item["icon"] = icon

        if count is not None:
            menu_item["count"] = count

        if endpoint:
            if endpoint[0] == "/":  # pragma: no cover
                menu_item["endpoint"] = endpoint
            elif perm or self.has_org_perm(endpoint):
                menu_item["endpoint"] = reverse(endpoint)

        if href:
            if href[0] == "/":
                menu_item["href"] = href
            elif perm or self.has_org_perm(href):
                menu_item["href"] = reverse(href)

        if items:  # pragma: no cover
            menu_item["items"] = [item for item in items if item is not None]

        # only include the menu item if we have somewhere to go
        if "href" not in menu_item and "endpoint" not in menu_item and not inline and not popup and not event:
            return None

        return menu_item

    def get_menu(self):
        return [item for item in self.derive_menu() if item is not None]

    def render_to_response(self, context, **response_kwargs):
        return JsonResponse({"results": self.get_menu()})


class BaseExportModal(ModalFormMixin, OrgPermsMixin, SmartFormView):
    """
    Base modal view for exports
    """

    class Form(forms.Form):
        MAX_FIELDS_COLS = 10
        MAX_GROUPS_COLS = 10

        start_date = TembaDateField(label=_("Start Date"))
        end_date = TembaDateField(label=_("End Date"))

        with_fields = forms.ModelMultipleChoiceField(
            ContactField.objects.none(),
            required=False,
            label=_("Fields"),
            widget=SelectMultipleWidget(attrs={"placeholder": _("Optional: Fields to include"), "searchable": True}),
        )
        with_groups = forms.ModelMultipleChoiceField(
            ContactGroup.objects.none(),
            required=False,
            label=_("Groups"),
            widget=SelectMultipleWidget(
                attrs={"placeholder": _("Optional: Group memberships to include"), "searchable": True}
            ),
        )

        def __init__(self, org, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.org = org
            self.fields["with_fields"].queryset = ContactField.get_fields(org).order_by(Lower("name"))
            self.fields["with_groups"].queryset = ContactGroup.get_groups(org=org, ready_only=True).order_by(
                Lower("name")
            )

        def clean_with_fields(self):
            data = self.cleaned_data["with_fields"]
            if data and len(data) > self.MAX_FIELDS_COLS:
                raise forms.ValidationError(_(f"You can only include up to {self.MAX_FIELDS_COLS} fields."))

            return data

        def clean_with_groups(self):
            data = self.cleaned_data["with_groups"]
            if data and len(data) > self.MAX_GROUPS_COLS:
                raise forms.ValidationError(_(f"You can only include up to {self.MAX_GROUPS_COLS} groups."))

            return data

        def clean(self):
            cleaned_data = super().clean()

            start_date = cleaned_data.get("start_date")
            end_date = cleaned_data.get("end_date")

            if start_date and start_date > timezone.now().astimezone(self.org.timezone).date():
                raise forms.ValidationError(_("Start date can't be in the future."))

            if end_date and start_date and end_date < start_date:
                raise forms.ValidationError(_("End date can't be before start date."))

            return cleaned_data

    form_class = Form
    submit_button_name = _("Export")
    success_message = _("We are preparing your export and you will get a notification when it is complete.")
    export_type = None
    readonly_servicing = False

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["org"] = self.request.org
        return kwargs

    def derive_initial(self):
        initial = super().derive_initial()

        # default to last 90 days in org timezone
        end = timezone.now()
        start = end - timedelta(days=90)

        initial["end_date"] = end.date()
        initial["start_date"] = start.date()
        return initial

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["blocker"] = self.get_blocker()
        return context

    def get_blocker(self) -> str:
        if self.export_type.has_recent_unfinished(self.request.org):
            return "existing-export"

        return ""

    def form_valid(self, form):
        if self.get_blocker():
            return self.form_invalid(form)

        user = self.request.user
        org = self.request.org
        export = self.create_export(org, user, form)

        on_transaction_commit(lambda: export.start())

        messages.info(self.request, self.success_message)

        return self.render_modal_response(form)


class BaseUsagesModal(DependencyMixin, OrgObjPermsMixin, SmartReadView):
    """
    Base view for usage modals of flow dependencies
    """

    slug_url_kwarg = "uuid"
    template_name = "orgs/dependency_usages_modal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["dependents"] = self.get_dependents(self.object)
        return context


class BaseDependencyDeleteModal(DependencyMixin, ModalFormMixin, OrgObjPermsMixin, SmartDeleteView):
    """
    Base view for delete modals of flow dependencies
    """

    slug_url_kwarg = "uuid"
    fields = ("uuid",)
    submit_button_name = _("Delete")
    template_name = "orgs/dependency_delete_modal.html"

    # warnings for soft dependencies
    type_warnings = {
        "flow": _("these may not work as expected"),  # always soft
        "campaign_event": _("these will be removed"),  # soft for fields and flows
        "trigger": _("these will be removed"),  # soft for flows
    }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # get dependents and sort by soft vs hard
        all_dependents = self.get_dependents(self.object)
        soft_dependents = {}
        hard_dependents = {}
        for type_key, type_qs in all_dependents.items():
            if type_key in self.object.soft_dependent_types:
                soft_dependents[type_key] = type_qs
            else:
                hard_dependents[type_key] = type_qs

        context["soft_dependents"] = soft_dependents
        context["hard_dependents"] = hard_dependents
        context["type_warnings"] = self.type_warnings
        return context

    def post(self, request, *args, **kwargs):
        obj = self.get_object()
        obj.release(request.user)

        messages.info(request, self.derive_success_message())
        response = HttpResponse()
        response["X-Temba-Success"] = self.get_success_url()
        return response
