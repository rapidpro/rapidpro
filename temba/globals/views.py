from gettext import gettext as _

from smartmin.views import SmartCreateView, SmartCRUDL, SmartListView, SmartUpdateView

from django import forms
from django.urls import reverse

from temba.orgs.models import Org
from temba.orgs.views import DependencyDeleteModal, DependencyUsagesModal, ModalMixin, OrgObjPermsMixin, OrgPermsMixin
from temba.utils.fields import InputWidget

from .models import Global


class CreateGlobalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        org_active_globals_limit = self.org.get_limit(Org.LIMIT_GLOBALS)
        if self.org.globals.filter(is_active=True).count() >= org_active_globals_limit:
            raise forms.ValidationError(
                _("Cannot create a new global as limit is %(limit)s."), params={"limit": org_active_globals_limit}
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

    class Create(ModalMixin, OrgPermsMixin, SmartCreateView):
        form_class = CreateGlobalForm
        success_message = ""
        submit_button_name = _("Create")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

        def form_valid(self, form):
            self.object = Global.get_or_create(
                self.request.user.get_org(),
                self.request.user,
                key=Global.make_key(name=form.cleaned_data["name"]),
                name=form.cleaned_data["name"],
                value=form.cleaned_data["value"],
            )

            return self.render_modal_response(form)

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateGlobalForm
        success_message = ""
        submit_button_name = _("Update")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

    class Delete(DependencyDeleteModal):
        cancel_url = "@globals.global_list"
        success_url = "@globals.global_list"
        success_message = ""

    class List(OrgPermsMixin, SmartListView):
        title = _("Manage Globals")
        fields = ("name", "key", "value")
        search_fields = ("name__icontains", "key__icontains")
        default_order = ("key",)
        paginate_by = 250

        def get_queryset(self, **kwargs):
            qs = super().get_queryset(**kwargs).filter(org=self.org, is_active=True)
            return Global.annotate_usage(qs)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org_globals = self.org.globals.filter(is_active=True)
            all_count = org_globals.count()

            if "HTTP_X_FORMAX" in self.request.META:
                context["global_count"] = all_count
            else:
                unused_count = Global.annotate_usage(org_globals).filter(usage_count=0).count()

                context["global_categories"] = [
                    {"label": _("All"), "count": all_count, "url": reverse("globals.global_list")},
                    {"label": _("Unused"), "count": unused_count, "url": reverse("globals.global_unused")},
                ]

            return context

    class Unused(List):
        def get_queryset(self, **kwargs):
            return super().get_queryset(**kwargs).filter(usage_count=0)

    class Usages(DependencyUsagesModal):
        permission = "globals.global_read"
