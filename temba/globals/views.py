from gettext import gettext as _

from smartmin.views import SmartCreateView, SmartCRUDL, SmartUpdateView

from django import forms
from django.urls import reverse

from temba.orgs.views.base import BaseDependencyDeleteModal, BaseListView, BaseUsagesModal
from temba.orgs.views.mixins import OrgObjPermsMixin, OrgPermsMixin
from temba.utils.fields import InputWidget
from temba.utils.views.mixins import ContextMenuMixin, ModalFormMixin, SpaMixin

from .models import Global


class CreateGlobalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        count, limit = Global.get_org_limit_progress(self.org)
        if limit is not None and count >= limit:
            raise forms.ValidationError(
                _(
                    "This workspace has reached its limit of %(limit)d globals. "
                    "You must delete existing ones before you can create new ones."
                ),
                params={"limit": limit},
            )

        return cleaned_data

    def clean_name(self):
        name = self.cleaned_data["name"]

        if not Global.is_valid_name(name):
            raise forms.ValidationError(_("Can only contain letters, numbers and hypens."))

        exists = self.org.globals.filter(is_active=True, name__iexact=name.lower()).exists()

        if self.instance.name != name and exists:
            raise forms.ValidationError(_("Must be unique."))

        if not Global.is_valid_key(Global.make_key(name)):
            raise forms.ValidationError(_("Isn't a valid name"))

        return name

    class Meta:
        model = Global
        fields = ("name", "value")
        widgets = {
            "name": InputWidget(attrs={"name": _("Name"), "widget_only": False}),
            "value": InputWidget(attrs={"name": _("Value"), "widget_only": False, "textarea": True}),
        }


class UpdateGlobalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    class Meta:
        model = Global
        fields = ("value",)
        widgets = {
            "value": InputWidget(attrs={"name": _("Value"), "widget_only": False, "textarea": True}),
        }


class GlobalCRUDL(SmartCRUDL):
    model = Global
    actions = ("create", "update", "delete", "list", "unused", "usages")

    class Create(ModalFormMixin, OrgPermsMixin, SmartCreateView):
        form_class = CreateGlobalForm
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def form_valid(self, form):
            self.object = Global.get_or_create(
                self.request.org,
                self.request.user,
                key=Global.make_key(name=form.cleaned_data["name"]),
                name=form.cleaned_data["name"],
                value=form.cleaned_data["value"],
            )

            return self.render_modal_response(form)

    class Update(ModalFormMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateGlobalForm
        submit_button_name = _("Update")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

    class Delete(BaseDependencyDeleteModal):
        cancel_url = "@globals.global_list"
        success_url = "@globals.global_list"

    class List(SpaMixin, ContextMenuMixin, BaseListView):
        title = _("Globals")
        fields = ("name", "key", "value")
        search_fields = ("name__icontains", "key__icontains")
        default_order = ("key",)
        paginate_by = 250
        menu_path = "/flow/globals"

        def build_context_menu(self, menu):
            if self.has_org_perm("globals.global_create"):
                menu.add_modax(
                    _("New"),
                    "new-global",
                    reverse("globals.global_create"),
                    title=_("New Global"),
                    as_button=True,
                    on_redirect="refreshGlobals()",
                )

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs)
            return Global.annotate_usage(qs)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org_globals = self.request.org.globals.filter(is_active=True)
            all_count = org_globals.count()
            unused_count = Global.annotate_usage(org_globals).filter(usage_count=0).count()

            context["global_categories"] = [
                {"label": _("All"), "count": all_count, "url": reverse("globals.global_list")},
                {"label": _("Unused"), "count": unused_count, "url": reverse("globals.global_unused")},
            ]

            return context

    class Unused(List):
        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(usage_count=0)

    class Usages(BaseUsagesModal):
        permission = "globals.global_read"
