from gettext import gettext as _

from smartmin.views import SmartCreateView, SmartCRUDL, SmartDeleteView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.conf import settings
from django.http import HttpResponseRedirect
from django.urls import reverse

from temba.orgs.views import ModalMixin, OrgObjPermsMixin, OrgPermsMixin

from .models import Global


class CreateGlobalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned_data = super().clean()

        if self.org.globals.filter(is_active=True).count() >= settings.MAX_ACTIVE_GLOBALS_PER_ORG:
            raise forms.ValidationError(
                _("Cannot create a new global as limit is %(limit)s."),
                params={"limit": settings.MAX_ACTIVE_GLOBALS_PER_ORG},
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


class UpdateGlobalForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        self.org = kwargs["org"]
        del kwargs["org"]

        super().__init__(*args, **kwargs)

    class Meta:
        model = Global
        fields = ("value",)


class GlobalCRUDL(SmartCRUDL):
    model = Global
    actions = ("create", "update", "delete", "list", "unused", "detail")

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

            response = self.render_to_response(
                self.get_context_data(
                    form=form, success_url=self.get_success_url(), success_script=getattr(self, "success_script", None)
                )
            )
            response["Temba-Success"] = self.get_success_url()
            return response

    class Update(ModalMixin, OrgObjPermsMixin, SmartUpdateView):
        form_class = UpdateGlobalForm
        success_message = ""
        submit_button_name = _("Update")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.derive_org()
            return kwargs

    class Delete(OrgObjPermsMixin, SmartDeleteView):
        cancel_url = "@globals.global_list"
        redirect_url = "@globals.global_list"
        success_message = ""
        http_method_names = ("get", "post")

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.pre_delete(self.object)
            redirect_url = self.get_redirect_url()

            # did it maybe change underneath us ???
            if self.object.get_usage_count():
                raise ValueError(f"Cannot remove a global {self.object.name} which is in use")

            self.object.release()

            return HttpResponseRedirect(redirect_url)

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

    class Detail(OrgObjPermsMixin, SmartReadView):
        template_name = "globals/global_detail.haml"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["dep_flows"] = list(self.object.dependent_flows.filter(is_active=True))
            return context
