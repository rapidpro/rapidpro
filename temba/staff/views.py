from smartmin.users.models import FailedLogin, PasswordHistory
from smartmin.users.views import UserUpdateForm
from smartmin.views import SmartCRUDL, SmartDeleteView, SmartListView, SmartReadView, SmartUpdateView

from django import forms
from django.contrib import messages
from django.contrib.auth.models import Group
from django.http import HttpResponse
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.orgs.models import User
from temba.utils import get_anonymous_user
from temba.utils.fields import SelectMultipleWidget
from temba.utils.views.mixins import ComponentFormMixin, ContextMenuMixin, ModalFormMixin, SpaMixin, StaffOnlyMixin


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
            response["Temba-Success"] = reverse("staff.user_list")
            return response

    class List(StaffOnlyMixin, SpaMixin, SmartListView):
        fields = ("email", "name", "date_joined")
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
            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["filter"] = self.request.GET.get("filter", "all")
            context["filters"] = self.filters
            return context
