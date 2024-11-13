from collections import OrderedDict

from smartmin.users.models import FailedLogin, PasswordHistory
from smartmin.users.views import UserUpdateForm
from smartmin.views import SmartCRUDL, SmartDeleteView, SmartFormView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.models import Group
from django.http import HttpResponse, HttpResponseRedirect
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.orgs.models import Org, OrgRole, User
from temba.orgs.views import switch_to_org
from temba.utils import get_anonymous_user
from temba.utils.fields import SelectMultipleWidget
from temba.utils.views.mixins import ComponentFormMixin, ContextMenuMixin, ModalFormMixin, SpaMixin, StaffOnlyMixin


class OrgCRUDL(SmartCRUDL):
    model = Org
    actions = ("read", "update", "list", "service")

    class Read(StaffOnlyMixin, SpaMixin, ContextMenuMixin, SmartReadView):
        def build_context_menu(self, menu):
            obj = self.get_object()
            if not obj.is_active:
                return

            menu.add_modax(
                _("Edit"),
                "update-workspace",
                reverse("staff.org_update", args=[obj.id]),
                title=_("Edit Workspace"),
                as_button=True,
                on_submit="handleWorkspaceUpdated()",
            )

            if not obj.is_flagged:
                menu.add_url_post(_("Flag"), f"{reverse('staff.org_update', args=[obj.id])}?action=flag")
            else:
                menu.add_url_post(_("Unflag"), f"{reverse('staff.org_update', args=[obj.id])}?action=unflag")

            if not obj.is_child:
                if not obj.is_suspended:
                    menu.add_url_post(_("Suspend"), f"{reverse('staff.org_update', args=[obj.id])}?action=suspend")
                else:
                    menu.add_url_post(_("Unsuspend"), f"{reverse('staff.org_update', args=[obj.id])}?action=unsuspend")

            if not obj.is_verified:
                menu.add_url_post(_("Verify"), f"{reverse('staff.org_update', args=[obj.id])}?action=verify")

            menu.new_group()
            menu.add_url_post(
                _("Service"),
                f'{reverse("staff.org_service")}?other_org={obj.id}&next={reverse("msgs.msg_inbox", args=[])}',
            )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.get_object()

            users_roles = []
            for role in OrgRole:
                role_users = list(org.get_users(roles=[role]).values("id", "email"))
                if role_users:
                    users_roles.append(dict(role_display=role.display_plural, users=role_users))

            context["users_roles"] = users_roles
            context["children"] = Org.objects.filter(parent=org, is_active=True).order_by("-created_on", "name")
            return context

    class List(StaffOnlyMixin, SpaMixin, SmartListView):
        fields = ("name", "owner", "timezone", "created_on")
        default_order = ("-created_on",)
        search_fields = ("name__icontains", "created_by__email__iexact", "config__icontains")
        link_fields = ("name", "owner")
        filters = (
            ("all", _("All"), dict(), ("-created_on",)),
            ("anon", _("Anonymous"), dict(is_anon=True, is_suspended=False), None),
            ("flagged", _("Flagged"), dict(is_flagged=True, is_suspended=False), None),
            ("suspended", _("Suspended"), dict(is_suspended=True), None),
            ("verified", _("Verified"), dict(config__verified=True, is_suspended=False), None),
        )

        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        def get_filter(self):
            obj_filter = self.request.GET.get("filter", "all")
            for filter in self.filters:
                if filter[0] == obj_filter:
                    return filter

        def derive_title(self):
            filter = self.get_filter()
            if filter:
                return filter[1]
            return super().derive_title()

        def derive_menu_path(self):
            return f"/staff/workspaces/{self.request.GET.get('filter', 'all')}"

        def get_owner(self, obj):
            owner = obj.get_owner()
            return f"{owner.name} ({owner.email})"

        def derive_queryset(self, **kwargs):
            qs = super().derive_queryset(**kwargs).filter(is_active=True)
            filter = self.get_filter()
            if filter:
                _, _, filter_kwargs, ordering = filter
                qs = qs.filter(**filter_kwargs)
                if ordering:
                    qs = qs.order_by(*ordering)
                else:
                    qs = qs.order_by(*self.default_order)
            else:
                qs = qs.filter(is_suspended=False).order_by(*self.default_order)

            return qs

        def derive_ordering(self):
            # we do this in derive queryset for simplicity
            return None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["filter"] = self.request.GET.get("filter", "all")
            context["filters"] = self.filters
            return context

        def lookup_field_link(self, context, field, obj):
            if field == "owner":
                owner = obj.get_owner()
                return reverse("staff.user_update", args=[owner.pk])
            return super().lookup_field_link(context, field, obj)

    class Update(StaffOnlyMixin, ModalFormMixin, ComponentFormMixin, SmartUpdateView):
        ACTION_FLAG = "flag"
        ACTION_UNFLAG = "unflag"
        ACTION_SUSPEND = "suspend"
        ACTION_UNSUSPEND = "unsuspend"
        ACTION_VERIFY = "verify"

        class Form(forms.ModelForm):
            features = forms.MultipleChoiceField(
                choices=Org.FEATURES_CHOICES, widget=SelectMultipleWidget(), required=False
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.limits_rows = []
                self.add_limits_fields(org)

            def clean(self):
                super().clean()

                limits = dict()
                for row in self.limits_rows:
                    if self.cleaned_data.get(row["limit_field_key"]):
                        limits[row["limit_type"]] = self.cleaned_data.get(row["limit_field_key"])

                self.cleaned_data["limits"] = limits

                return self.cleaned_data

            def add_limits_fields(self, org: Org):
                for limit_type in settings.ORG_LIMIT_DEFAULTS.keys():
                    field = forms.IntegerField(
                        label=limit_type.capitalize(),
                        required=False,
                        initial=org.limits.get(limit_type),
                        widget=forms.TextInput(attrs={"placeholder": _("Limit")}),
                    )
                    field_key = f"{limit_type}_limit"

                    self.fields.update(OrderedDict([(field_key, field)]))
                    self.limits_rows.append({"limit_type": limit_type, "limit_field_key": field_key})

            class Meta:
                model = Org
                fields = ("name", "features", "is_anon")

        form_class = Form
        success_url = "id@staff.org_read"

        def derive_title(self):
            return None

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.get_object()
            return kwargs

        def post(self, request, *args, **kwargs):
            if "action" in request.POST:
                action = request.POST["action"]
                obj = self.get_object()

                if action == self.ACTION_FLAG:
                    obj.flag()
                elif action == self.ACTION_UNFLAG:
                    obj.unflag()
                elif action == self.ACTION_SUSPEND:
                    obj.suspend()
                elif action == self.ACTION_UNSUSPEND:
                    obj.unsuspend()
                elif action == self.ACTION_VERIFY:
                    obj.verify()

                return HttpResponseRedirect(reverse("staff.org_read", args=[obj.id]))

            return super().post(request, *args, **kwargs)

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            cleaned_data = self.form.cleaned_data

            obj.limits = cleaned_data["limits"]
            return obj

    class Service(StaffOnlyMixin, SmartFormView):
        class ServiceForm(forms.Form):
            other_org = forms.ModelChoiceField(queryset=Org.objects.all(), widget=forms.HiddenInput())
            next = forms.CharField(widget=forms.HiddenInput(), required=False)

        form_class = ServiceForm
        fields = ("other_org", "next")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["other_org"] = Org.objects.filter(id=self.request.GET.get("other_org")).first()
            context["next"] = self.request.GET.get("next", "")
            return context

        def derive_initial(self):
            initial = super().derive_initial()
            initial["other_org"] = self.request.GET.get("other_org", "")
            initial["next"] = self.request.GET.get("next", "")
            return initial

        # valid form means we set our org and redirect to their inbox
        def form_valid(self, form):
            switch_to_org(self.request, form.cleaned_data["other_org"], servicing=True)
            success_url = form.cleaned_data["next"] or reverse("msgs.msg_inbox")
            return HttpResponseRedirect(success_url)

        # invalid form login 'logs out' the user from the org and takes them to the root
        def form_invalid(self, form):
            switch_to_org(self.request, None)
            return HttpResponseRedirect(reverse("staff.org_list"))


class UserCRUDL(SmartCRUDL):
    model = User
    actions = ("read", "update", "delete", "list")

    class Read(StaffOnlyMixin, ContextMenuMixin, SpaMixin, SmartReadView):
        fields = ("email", "date_joined")
        menu_path = "/staff/users/all"

        def build_context_menu(self, menu):
            obj = self.get_object()
            menu.add_modax(
                _("Edit"),
                "user-update",
                reverse("staff.user_update", args=[obj.id]),
                title=_("Edit User"),
                as_button=True,
            )

            menu.add_modax(
                _("Delete"), "user-delete", reverse("staff.user_delete", args=[obj.id]), title=_("Delete User")
            )

    class Update(StaffOnlyMixin, ModalFormMixin, ComponentFormMixin, ContextMenuMixin, SmartUpdateView):
        class Form(UserUpdateForm):
            groups = forms.ModelMultipleChoiceField(
                widget=SelectMultipleWidget(
                    attrs={"placeholder": _("Optional: Select permissions groups."), "searchable": True}
                ),
                queryset=Group.objects.all(),
                required=False,
            )

            class Meta:
                model = User
                fields = ("email", "new_password", "first_name", "last_name", "groups")
                help_texts = {"new_password": _("You can reset the user's password by entering a new password here")}

        form_class = Form
        success_message = "User updated successfully."
        title = "Update User"

        def pre_save(self, obj):
            obj.username = obj.email
            return obj

        def post_save(self, obj):
            """
            Make sure our groups are up-to-date
            """
            if "groups" in self.form.cleaned_data:
                obj.groups.clear()
                for group in self.form.cleaned_data["groups"]:
                    obj.groups.add(group)

            # if a new password was set, reset our failed logins
            if "new_password" in self.form.cleaned_data and self.form.cleaned_data["new_password"]:
                FailedLogin.objects.filter(username__iexact=self.object.username).delete()
                PasswordHistory.objects.create(user=obj, password=obj.password)

            return obj

    class Delete(StaffOnlyMixin, ModalFormMixin, SmartDeleteView):
        fields = ("id",)
        permission = "staff.user_update"
        submit_button_name = _("Delete")
        cancel_url = "@staff.user_list"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["owned_orgs"] = self.get_object().get_owned_orgs()
            return context

        def post(self, request, *args, **kwargs):
            user = self.get_object()
            user.release(self.request.user)

            messages.info(request, self.derive_success_message())
            response = HttpResponse()
            response["X-Temba-Success"] = reverse("staff.user_list")
            return response

    class List(StaffOnlyMixin, SpaMixin, SmartListView):
        fields = ("email", "name", "date_joined", "2fa")
        ordering = ("-date_joined",)
        search_fields = ("email__icontains", "first_name__icontains", "last_name__icontains")
        filters = (("all", _("All")), ("beta", _("Beta")), ("staff", _("Staff")))

        def derive_menu_path(self):
            return f"/staff/users/{self.request.GET.get('filter', 'all')}"

        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        def derive_queryset(self, **kwargs):
            qs = super().derive_queryset(**kwargs).filter(is_active=True).exclude(id=get_anonymous_user().id)

            obj_filter = self.request.GET.get("filter")
            if obj_filter == "beta":
                qs = qs.filter(groups__name="Beta")
            elif obj_filter == "staff":
                qs = qs.filter(is_staff=True)

            return qs.select_related("settings")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["filter"] = self.request.GET.get("filter", "all")
            context["filters"] = self.filters
            return context

        def get_2fa(self, obj):
            return _("Yes") if obj.settings.two_factor_enabled else _("No")
