import itertools
import smtplib
from collections import OrderedDict
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import parse_qs, quote, quote_plus, unquote, urlparse

import iso8601
import pyotp
from packaging.version import Version
from smartmin.users.models import FailedLogin, PasswordHistory
from smartmin.users.views import Login, UserUpdateForm
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartListView,
    SmartModelActionView,
    SmartModelFormView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView as AuthLoginView
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import IntegrityError
from django.db.models.functions import Lower
from django.forms import ModelChoiceField
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import resolve_url
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import DjangoUnicodeDecodeError, force_str
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.api.models import APIToken, Resthook
from temba.campaigns.models import Campaign
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.notifications.mixins import NotificationTargetMixin
from temba.orgs.tasks import send_user_verification_email
from temba.utils import analytics, get_anonymous_user, json, languages, str_to_bool
from temba.utils.email import is_valid_address
from temba.utils.fields import ArbitraryJsonChoiceField, CheckboxWidget, InputWidget, SelectMultipleWidget, SelectWidget
from temba.utils.timezones import TimeZoneFormField
from temba.utils.views import (
    ComponentFormMixin,
    ContentMenuMixin,
    NonAtomicMixin,
    NoNavMixin,
    PostOnlyMixin,
    RequireRecentAuthMixin,
    SpaMixin,
    StaffOnlyMixin,
)

from .models import BackupToken, Export, IntegrationType, Invitation, Org, OrgImport, OrgRole, User, UserSettings

# session key for storing a two-factor enabled user's id once we've checked their password
TWO_FACTOR_USER_SESSION_KEY = "_two_factor_user_id"
TWO_FACTOR_STARTED_SESSION_KEY = "_two_factor_started_on"
TWO_FACTOR_LIMIT_SECONDS = 5 * 60


def switch_to_org(request, org):
    request.session["org_id"] = org.id if org else None


def check_login(request):
    """
    Simple view that checks whether we actually need to log in.  This is needed on the live site
    because we serve the main page as http:// but the logged in pages as https:// and only store
    the cookies on the SSL connection.  This view will be called in https:// land where we will
    check whether we are logged in, if so then we will redirect to the LOGIN_URL, otherwise we take
    them to the normal user login page
    """
    if request.user.is_authenticated:
        return HttpResponseRedirect(settings.LOGIN_REDIRECT_URL)
    else:
        return HttpResponseRedirect(settings.LOGIN_URL)


class OrgPermsMixin:
    """
    Get the organization and the user within the inheriting view so that it be come easy to decide
    whether this user has a certain permission for that particular organization to perform the view's actions
    """

    def get_user(self):
        return self.request.user

    def derive_org(self):
        return self.request.org

    def has_org_perm(self, permission):
        org = self.derive_org()
        if org:
            return self.get_user().has_org_perm(org, permission)
        return False

    def has_permission(self, request, *args, **kwargs):
        """
        Figures out if the current user has permissions for this view.
        """
        self.kwargs = kwargs
        self.args = args
        self.request = request

        org = self.derive_org()

        if self.get_user().is_staff and org:
            return True

        if self.get_user().is_anonymous:
            return False

        if self.get_user().has_perm(self.permission):  # pragma: needs cover
            return True

        return self.has_org_perm(self.permission)

    def dispatch(self, request, *args, **kwargs):
        # non admin authenticated users without orgs get the org chooser
        user = self.get_user()
        if user.is_authenticated and not user.is_staff:
            if not self.derive_org():
                return HttpResponseRedirect(reverse("orgs.org_choose"))

        return super().dispatch(request, *args, **kwargs)


class OrgFilterMixin:
    """
    Simple mixin to filter a view's queryset by the request org
    """

    def derive_queryset(self, *args, **kwargs):
        queryset = super().derive_queryset(*args, **kwargs)

        if not self.request.user.is_authenticated:
            return queryset.none()  # pragma: no cover
        else:
            return queryset.filter(org=self.request.org)


class OrgObjPermsMixin(OrgPermsMixin):
    def get_object_org(self):
        return self.get_object().org

    def has_org_perm(self, codename):
        has_org_perm = super().has_org_perm(codename)
        if has_org_perm:
            return self.request.org == self.get_object_org()

        return False

    def has_permission(self, request, *args, **kwargs):
        user = self.request.user
        if user.is_staff:
            return True

        has_perm = super().has_permission(request, *args, **kwargs)
        if has_perm:
            return self.request.org == self.get_object_org()

    def pre_process(self, request, *args, **kwargs):
        org = self.get_object_org()
        if request.user.is_staff and self.request.org != org:
            return HttpResponseRedirect(
                f"{reverse('orgs.org_service')}?next={quote_plus(request.path)}&other_org={org.id}"
            )


class ModalMixin(SmartFormView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if "HTTP_X_PJAX" in self.request.META and "HTTP_X_FORMAX" not in self.request.META:  # pragma: no cover
            context["base_template"] = "smartmin/modal.html"
            context["is_modal"] = True
        if "success_url" in kwargs:  # pragma: no cover
            context["success_url"] = kwargs["success_url"]

        pairs = [quote(k) + "=" + quote(v) for k, v in self.request.GET.items() if k != "_"]
        context["action_url"] = self.request.path + "?" + ("&".join(pairs))

        return context

    def render_modal_response(self, form=None):
        success_url = self.get_success_url()
        response = self.render_to_response(
            self.get_context_data(
                form=form,
                success_url=self.get_success_url(),
                success_script=getattr(self, "success_script", None),
            )
        )

        response["Temba-Success"] = success_url
        return response

    def form_valid(self, form):
        if isinstance(form, forms.ModelForm):
            self.object = form.save(commit=False)

        try:
            if isinstance(self, SmartModelFormView):
                self.object = self.pre_save(self.object)
                self.save(self.object)
                self.object = self.post_save(self.object)

            elif isinstance(self, SmartModelActionView):
                self.execute_action()

            messages.success(self.request, self.derive_success_message())

            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                return self.render_modal_response(form)

        except (IntegrityError, ValueError, ValidationError) as e:
            message = getattr(e, "message", str(e).capitalize())
            self.form.add_error(None, message)
            return self.render_to_response(self.get_context_data(form=form))


class IntegrationViewMixin(OrgPermsMixin):
    permission = "orgs.org_manage_integrations"
    integration_type = None

    def __init__(self, integration_type):
        self.integration_type = integration_type
        super().__init__()

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["integration_type"] = self.integration_type
        context["integration_connected"] = self.integration_type.is_connected(self.request.org)
        return context


class IntegrationFormaxView(IntegrationViewMixin, ComponentFormMixin, SmartFormView):
    class Form(forms.Form):
        def __init__(self, request, integration_type, **kwargs):
            self.request = request
            self.channel_type = integration_type
            super().__init__(**kwargs)

    success_url = "@orgs.org_workspace"

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["integration_type"] = self.integration_type
        return kwargs

    def form_valid(self, form):
        response = self.render_to_response(self.get_context_data(form=form))
        response["REDIRECT"] = self.get_success_url()
        return response


class DependencyModalMixin(OrgObjPermsMixin):
    dependent_order = {"campaign_event": ("relative_to__name",), "trigger": ("trigger_type", "created_on")}
    dependent_select_related = {"campaign_event": ("campaign", "relative_to")}

    def get_dependents(self, obj) -> dict:
        dependents = {}
        for type_key, type_qs in obj.get_dependents().items():
            # only include dependency types which we have at least one dependent of
            if type_qs.exists():
                type_qs = type_qs.order_by(*self.dependent_order.get(type_key, ("name",)))

                type_select_related = self.dependent_select_related.get(type_key, ())
                if type_select_related:
                    type_qs = type_qs.select_related(*type_select_related)

                dependents[type_key] = type_qs
        return dependents


class DependencyUsagesModal(DependencyModalMixin, SmartReadView):
    """
    Base view for usage modals of flow dependencies
    """

    slug_url_kwarg = "uuid"
    template_name = "orgs/dependency_usages_modal.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["dependents"] = self.get_dependents(self.object)
        return context


class DependencyDeleteModal(DependencyModalMixin, ModalMixin, SmartDeleteView):
    """
    Base view for delete modals of flow dependencies
    """

    slug_url_kwarg = "uuid"
    fields = ("uuid",)
    success_message = ""
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
        response["Temba-Success"] = self.get_success_url()
        return response


class OrgSignupForm(forms.ModelForm):
    """
    Signup for new organizations
    """

    first_name = forms.CharField(
        max_length=User._meta.get_field("first_name").max_length,
        widget=InputWidget(attrs={"widget_only": True, "placeholder": _("First name")}),
    )
    last_name = forms.CharField(
        max_length=User._meta.get_field("last_name").max_length,
        widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Last name")}),
    )
    email = forms.EmailField(
        max_length=User._meta.get_field("username").max_length,
        widget=InputWidget(attrs={"widget_only": True, "placeholder": _("name@domain.com")}),
    )

    timezone = TimeZoneFormField(help_text=_("The timezone for your workspace"), widget=forms.widgets.HiddenInput())

    password = forms.CharField(
        widget=InputWidget(attrs={"hide_label": True, "password": True, "placeholder": _("Password")}),
        validators=[validate_password],
        help_text=_("At least eight characters or more"),
    )

    name = forms.CharField(
        label=_("Workspace"),
        help_text=_("A workspace is usually the name of a company or project"),
        widget=InputWidget(attrs={"widget_only": False, "placeholder": _("My Company, Inc.")}),
    )

    def clean_email(self):
        email = self.cleaned_data["email"]
        if email:
            if User.objects.filter(username__iexact=email):
                raise forms.ValidationError(_("That email address is already used"))

        return email.lower()

    class Meta:
        model = Org
        fields = ("first_name", "last_name", "email", "timezone", "password", "name")


class OrgGrantForm(forms.ModelForm):
    first_name = forms.CharField(
        help_text=_("The first name of the workspace administrator"),
        max_length=User._meta.get_field("first_name").max_length,
    )
    last_name = forms.CharField(
        help_text=_("Your last name of the workspace administrator"),
        max_length=User._meta.get_field("last_name").max_length,
    )
    email = forms.EmailField(help_text=_("Their email address"), max_length=User._meta.get_field("username").max_length)
    timezone = TimeZoneFormField(help_text=_("The timezone for the workspace"))
    password = forms.CharField(
        widget=forms.PasswordInput,
        required=False,
        help_text=_("Their password, at least eight letters please. (leave blank for existing login)"),
    )
    name = forms.CharField(label=_("Workspace"), help_text=_("The name of the new workspace"))

    def clean(self):
        data = self.cleaned_data

        email = data.get("email", None)
        password = data.get("password", None)

        # for granting new accounts, either the email maps to an existing user (and their existing password is used)
        # or both email and password must be included
        if email:
            user = User.objects.filter(username__iexact=email).first()
            if user:
                if password:
                    raise ValidationError(_("Login already exists, please do not include password."))
            else:
                if not password:
                    raise ValidationError(_("Password required for new login."))

                validate_password(password)

        return data

    class Meta:
        model = Org
        fields = ("first_name", "last_name", "email", "timezone", "password", "name")


class LoginView(Login):
    """
    Overrides the smartmin login view to redirect users with 2FA enabled to a second verification view.
    """

    template_name = "orgs/login/login.html"

    def form_valid(self, form):
        user = form.get_user()

        if user.settings.two_factor_enabled:
            self.request.session[TWO_FACTOR_USER_SESSION_KEY] = str(user.id)
            self.request.session[TWO_FACTOR_STARTED_SESSION_KEY] = timezone.now().isoformat()

            verify_url = reverse("users.two_factor_verify")
            redirect_url = self.get_redirect_url()
            if redirect_url:
                verify_url += f"?{self.redirect_field_name}={quote(redirect_url)}"

            return HttpResponseRedirect(verify_url)

        user.record_auth()
        return super().form_valid(form)


class BaseTwoFactorView(AuthLoginView):
    def dispatch(self, request, *args, **kwargs):
        # redirect back to login view if user hasn't completed that yet
        user = self.get_user()
        if not user:
            return HttpResponseRedirect(reverse("users.login"))

        return super().dispatch(request, *args, **kwargs)

    def get_user(self):
        user_id = self.request.session.get(TWO_FACTOR_USER_SESSION_KEY)
        started_on = self.request.session.get(TWO_FACTOR_STARTED_SESSION_KEY)
        if user_id and started_on:
            # only return user if two factor process was started recently
            started_on = iso8601.parse_date(started_on)
            if started_on >= timezone.now() - timedelta(seconds=TWO_FACTOR_LIMIT_SECONDS):
                return User.objects.filter(id=user_id, is_active=True).first()
        return None

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.get_user()
        return kwargs

    def form_invalid(self, form):
        user = self.get_user()

        # apply the same limits on failed attempts that smartmin uses for regular logins
        lockout_timeout = getattr(settings, "USER_LOCKOUT_TIMEOUT", 10)
        failed_login_limit = getattr(settings, "USER_FAILED_LOGIN_LIMIT", 5)

        FailedLogin.objects.create(username=user.username)

        bad_interval = timezone.now() - timedelta(minutes=lockout_timeout)
        failures = FailedLogin.objects.filter(username__iexact=user.username)

        # if the failures reset after a period of time, then limit our query to that interval
        if lockout_timeout > 0:
            failures = failures.filter(failed_on__gt=bad_interval)

        # if there are too many failed logins, take them to the failed page
        if failures.count() >= failed_login_limit:
            self.reset_user()

            return HttpResponseRedirect(reverse("users.user_failed"))

        return super().form_invalid(form)

    def form_valid(self, form):
        user = self.get_user()

        # set the user as actually authenticated now
        login(self.request, user)
        user.record_auth()

        # remove our session key so if the user comes back this page they'll get directed to the login view
        self.reset_user()

        # cleanup any failed logins
        FailedLogin.objects.filter(username__iexact=user.username).delete()

        return HttpResponseRedirect(self.get_success_url())

    def reset_user(self):
        self.request.session.pop(TWO_FACTOR_USER_SESSION_KEY, None)
        self.request.session.pop(TWO_FACTOR_STARTED_SESSION_KEY, None)


class TwoFactorVerifyView(BaseTwoFactorView):
    """
    View to let users with 2FA enabled verify their identity via an OTP from a device.
    """

    class Form(forms.Form):
        otp = forms.CharField(max_length=6, required=True)

        def __init__(self, request, user, *args, **kwargs):
            self.user = user
            super().__init__(*args, **kwargs)

        def clean_otp(self):
            data = self.cleaned_data["otp"]
            if not self.user.verify_2fa(otp=data):
                raise ValidationError(_("Incorrect OTP. Please try again."))
            return data

    form_class = Form
    template_name = "orgs/login/two_factor_verify.html"


class TwoFactorBackupView(BaseTwoFactorView):
    """
    View to let users with 2FA enabled verify their identity using a backup token.
    """

    class Form(forms.Form):
        token = forms.CharField(max_length=8, required=True)

        def __init__(self, request, user, *args, **kwargs):
            self.user = user
            super().__init__(*args, **kwargs)

        def clean_token(self):
            data = self.cleaned_data["token"]
            if not self.user.verify_2fa(backup_token=data):
                raise ValidationError(_("Invalid backup token. Please try again."))
            return data

    form_class = Form
    template_name = "orgs/login/two_factor_backup.html"


class ConfirmAccessView(Login):
    """
    Overrides the smartmin login view to provide a view for an already logged in user to re-authenticate.
    """

    class Form(forms.Form):
        password = forms.CharField(
            label=" ", widget=InputWidget(attrs={"placeholder": _("Password"), "password": True}), required=True
        )

        def __init__(self, request, *args, **kwargs):
            super().__init__(*args, **kwargs)

            self.user = request.user

        def clean_password(self):
            data = self.cleaned_data["password"]
            if not self.user.check_password(data):
                raise forms.ValidationError(_("Password incorrect."))
            return data

        def get_user(self):
            return self.user

    template_name = "orgs/login/confirm_access.html"
    form_class = Form

    def dispatch(self, request, *args, **kwargs):
        if not self.request.user.is_authenticated:
            return HttpResponseRedirect(resolve_url(settings.LOGIN_URL))

        return super().dispatch(request, *args, **kwargs)

    def get_username(self, form):
        return self.request.user.username

    def form_valid(self, form):
        form.get_user().record_auth()

        return super().form_valid(form)


class InferOrgMixin:
    """
    Mixin for view whose object is the current org
    """

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.org


class InferUserMixin:
    """
    Mixin for view whose object is the current user
    """

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.user


class UserCRUDL(SmartCRUDL):
    model = User
    actions = (
        "list",
        "update",
        "edit",
        "delete",
        "read",
        "forget",
        "two_factor_enable",
        "two_factor_disable",
        "two_factor_tokens",
        "account",
        "token",
        "verify_email",
        "send_verification_email",
    )

    class Read(StaffOnlyMixin, ContentMenuMixin, SpaMixin, SmartReadView):
        fields = ("email", "date_joined")
        menu_path = "/staff/users/all"

        def build_content_menu(self, menu):
            obj = self.get_object()
            menu.add_modax(
                _("Edit"),
                "user-update",
                reverse("orgs.user_update", args=[obj.id]),
                title=_("Edit User"),
                as_button=True,
            )

            menu.add_modax(
                _("Delete"),
                "user-delete",
                reverse("orgs.user_delete", args=[obj.id]),
                title=_("Delete User"),
            )

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

    class Update(StaffOnlyMixin, SpaMixin, ModalMixin, ComponentFormMixin, ContentMenuMixin, SmartUpdateView):
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

    class Delete(StaffOnlyMixin, SpaMixin, ModalMixin, SmartDeleteView):
        fields = ("id",)
        permission = "orgs.user_update"
        submit_button_name = _("Delete")
        cancel_url = "@orgs.user_list"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["owned_orgs"] = self.get_object().get_owned_orgs()
            return context

        def post(self, request, *args, **kwargs):
            user = self.get_object()
            user.release(self.request.user)

            messages.info(request, self.derive_success_message())
            response = HttpResponse()
            response["Temba-Success"] = reverse("orgs.user_list")
            return response

    class Forget(SmartFormView):
        class ForgetForm(forms.Form):
            email = forms.EmailField(required=True, label=_("Your Email"), widget=InputWidget())

            def clean_email(self):
                email = self.cleaned_data["email"].lower().strip()
                return email

        title = _("Password Recovery")
        form_class = ForgetForm
        permission = None
        success_message = _("An email has been sent to your account with further instructions.")
        success_url = "@users.user_login"
        fields = ("email",)

        def form_valid(self, form):
            email = form.cleaned_data["email"]
            user = User.objects.filter(email__iexact=email).first()

            if user:
                user.recover_password(self.request.branding)
            else:
                # No user, check if we have an invite for the email and resend that
                existing_invite = Invitation.objects.filter(is_active=True, email__iexact=email).first()
                if existing_invite:
                    existing_invite.send()

            return super().form_valid(form)

    class Edit(InferUserMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            first_name = forms.CharField(
                label=_("First Name"), widget=InputWidget(attrs={"placeholder": _("Required")})
            )
            last_name = forms.CharField(label=_("Last Name"), widget=InputWidget(attrs={"placeholder": _("Required")}))
            email = forms.EmailField(required=True, label=_("Email"), widget=InputWidget())
            current_password = forms.CharField(
                required=False,
                label=_("Current Password"),
                widget=InputWidget({"widget_only": True, "placeholder": _("Password Required"), "password": True}),
            )
            new_password = forms.CharField(
                required=False,
                label=_("New Password"),
                widget=InputWidget(attrs={"placeholder": _("Optional"), "password": True}),
            )
            language = forms.ChoiceField(
                choices=settings.LANGUAGES, required=True, label=_("Website Language"), widget=SelectWidget()
            )

            def clean_new_password(self):
                password = self.cleaned_data["new_password"]
                if password and not len(password) >= 8:
                    raise forms.ValidationError(_("Passwords must have at least 8 letters."))
                return password

            def clean_current_password(self):
                user = self.instance
                password = self.cleaned_data.get("current_password", None)

                # password is required to change your email address or set a new password
                if self.data.get("new_password", None) or self.data.get("email", None) != user.email:
                    if not user.check_password(password):
                        raise forms.ValidationError(_("Please enter your password to save changes."))

                return password

            def clean_email(self):
                user = self.instance
                email = self.cleaned_data["email"].lower()

                if User.objects.filter(username=email).exclude(pk=user.pk):
                    raise forms.ValidationError(_("Sorry, that email address is already taken."))

                return email

            class Meta:
                model = User
                fields = ("first_name", "last_name", "email", "current_password", "new_password", "language")

        form_class = Form
        success_message = ""

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def derive_initial(self):
            initial = super().derive_initial()
            initial["language"] = self.get_object().settings.language
            return initial

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            # keep our username and email in sync and record if email is changing
            obj.username = obj.email
            obj._email_changed = obj.email != User.objects.get(id=obj.id).email

            if self.form.cleaned_data["new_password"]:
                obj.set_password(self.form.cleaned_data["new_password"])

            return obj

        def post_save(self, obj):
            # save the user settings as well
            obj = super().post_save(obj)

            if obj._email_changed:
                obj.settings.email_status = UserSettings.STATUS_UNVERIFIED

            obj.settings.language = self.form.cleaned_data["language"]
            obj.settings.save(update_fields=("language", "email_status"))

            return obj

    class SendVerificationEmail(SpaMixin, PostOnlyMixin, InferUserMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            class Meta:
                model = User
                fields = ()

        form_class = Form
        submit_button_name = _("Send Verification Email")
        menu_path = "/settings/account"
        success_url = "@orgs.user_account"
        success_message = _("Verification email sent")

        def has_permission(self, request, *args, **kwargs):
            return request.user.is_authenticated

        def pre_process(self, request, *args, **kwargs):
            if request.user.settings.email_status == UserSettings.STATUS_VERIFIED:
                return HttpResponseRedirect(reverse("orgs.user_account"))

            return super().pre_process(request, *args, **kwargs)

        def form_valid(self, form):
            send_user_verification_email.delay(self.get_object().id)
            return super().form_valid(form)

    class VerifyEmail(NoNavMixin, SmartReadView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<secret>\w+)/$" % (path, action)

        def get_object(self, *args, **kwargs):
            return self.request.user

        def has_permission(self, request, *args, **kwargs):
            return request.user.is_authenticated

        @cached_property
        def email_user(self, **kwargs):
            user_settings = UserSettings.objects.filter(email_verification_secret=self.kwargs["secret"]).first()
            return user_settings.user if user_settings else None

        def pre_process(self, request, *args, **kwargs):
            is_verified = self.request.user.settings.email_status == UserSettings.STATUS_VERIFIED

            if self.email_user == self.request.user and not is_verified:
                self.request.user.settings.email_status = UserSettings.STATUS_VERIFIED
                self.request.user.settings.save(update_fields=("email_status",))

            return super().pre_process(request, *args, **kwargs)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["email_user"] = self.email_user
            context["email_secret"] = self.kwargs["secret"]
            return context

    class TwoFactorEnable(SpaMixin, InferUserMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            otp = forms.CharField(
                label=_("The generated OTP"),
                widget=InputWidget(attrs={"placeholder": _("6-digit code")}),
                max_length=6,
                required=True,
            )
            confirm_password = forms.CharField(
                label=_("Your current login password"),
                widget=InputWidget(attrs={"placeholder": _("Current password"), "password": True}),
                required=True,
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.user = user

            def clean_otp(self):
                data = self.cleaned_data["otp"]
                if not self.user.verify_2fa(otp=data):
                    raise forms.ValidationError(_("OTP incorrect. Please try again."))
                return data

            def clean_confirm_password(self):
                data = self.cleaned_data["confirm_password"]
                if not self.user.check_password(data):
                    raise forms.ValidationError(_("Password incorrect."))
                return data

            class Meta:
                model = User
                fields = ("otp", "confirm_password")

        form_class = Form
        menu_path = "/settings/account"
        title = _("Enable Two-factor Authentication")
        submit_button_name = _("Enable")
        success_message = ""
        success_url = "@orgs.user_two_factor_tokens"

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            brand = self.request.branding["name"]
            user = self.request.user
            secret_url = pyotp.TOTP(user.settings.otp_secret).provisioning_uri(user.username, issuer_name=brand)

            context["secret_url"] = secret_url
            return context

        def form_valid(self, form):
            self.request.user.enable_2fa()
            self.request.user.record_auth()

            return super().form_valid(form)

    class TwoFactorDisable(SpaMixin, InferUserMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            confirm_password = forms.CharField(
                label=" ",
                widget=InputWidget(attrs={"placeholder": _("Current password"), "password": True}),
                required=True,
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.user = user

            def clean_confirm_password(self):
                data = self.cleaned_data["confirm_password"]
                if not self.user.check_password(data):
                    raise forms.ValidationError(_("Password incorrect."))
                return data

            class Meta:
                model = User
                fields = ("confirm_password",)

        form_class = Form
        menu_path = "/settings/account"
        title = _("Disable Two-factor Authentication")
        submit_button_name = _("Disable")
        success_message = ""
        success_url = "@orgs.user_account"

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            self.request.user.disable_2fa()
            self.request.user.record_auth()

            return super().form_valid(form)

    class TwoFactorTokens(SpaMixin, RequireRecentAuthMixin, SmartTemplateView):
        title = _("Two-factor Authentication")
        menu_path = "/settings/account"

        def pre_process(self, request, *args, **kwargs):
            # if 2FA isn't enabled for this user, take them to the enable view instead
            if not self.request.user.settings.two_factor_enabled:
                return HttpResponseRedirect(reverse("orgs.user_two_factor_enable"))

            return super().pre_process(request, *args, **kwargs)

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def post(self, request, *args, **kwargs):
            BackupToken.generate_for_user(self.request.user)
            messages.info(request, _("Two-factor authentication backup tokens changed."))

            return super().get(request, *args, **kwargs)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["backup_tokens"] = self.request.user.backup_tokens.order_by("id")
            return context

    class Account(SpaMixin, FormaxMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Account")
        menu_path = "/settings/account"

        def has_permission(self, request):
            return request.user.is_authenticated

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["two_factor_enabled"] = self.request.user.settings.two_factor_enabled
            return context

        def derive_formax_sections(self, formax, context):
            formax.add_section("profile", reverse("orgs.user_edit"), icon="user")

            if self.has_org_perm("orgs.user_token"):
                formax.add_section("token", reverse("orgs.user_token"), icon="upload", nobutton=True)

    class Token(InferUserMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            class Meta:
                model = User
                fields = ()

        form_class = Form
        submit_button_name = _("Regenerate")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["api_token"] = self.request.user.get_api_token(self.request.org)
            return context

        def form_valid(self, form):
            APIToken.get_or_create(self.request.org, self.request.user, refresh=True)

            return super().form_valid(form)


class MenuMixin(OrgPermsMixin):
    def create_divider(self):
        return {"type": "divider"}

    def create_space(self):  # pragma: no cover
        return {"type": "space"}

    def create_section(self, name, items=()):  # pragma: no cover
        return {"id": slugify(name), "name": name, "type": "section", "items": items}

    def create_list(self, name, href, type):
        return {"id": name, "href": href, "type": type}

    # TODO: Decide whether we want to keep this at all
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
        event=False,
        posterize=False,
        bubble=None,
    ):
        if perm and not self.has_org_perm(perm):  # pragma: no cover
            return

        menu_item = {"name": name, "inline": inline}
        menu_item["id"] = menu_id if menu_id else slugify(name)
        menu_item["bottom"] = bottom
        menu_item["popup"] = popup
        menu_item["avatar"] = avatar
        menu_item["posterize"] = posterize

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


class InvitationMixin:
    @cached_property
    def invitation(self, **kwargs):
        return Invitation.objects.filter(secret=self.kwargs["secret"], is_active=True).first()

    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/(?P<secret>\w+)/$" % (path, action)

    def pre_process(self, request, *args, **kwargs):
        if not self.invitation:
            messages.info(request, _("Your invitation link is invalid. Please contact your workspace administrator."))
            return HttpResponseRedirect(reverse("public.public_index"))

        return super().pre_process(request, *args, **kwargs)

    def get_object(self, **kwargs):
        return self.invitation.org

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["invitation"] = self.invitation
        return context


class OrgCRUDL(SmartCRUDL):
    actions = (
        "signup",
        "start",
        "read",
        "edit",
        "edit_sub_org",
        "join",
        "join_signup",
        "join_accept",
        "grant",
        "choose",
        "delete_child",
        "manage_accounts",
        "manage_accounts_sub_org",
        "manage",
        "menu",
        "update",
        "country",
        "languages",
        "sub_orgs",
        "create",
        "export",
        "prometheus",
        "resthooks",
        "service",
        "surveyor",
        "smtp_server",
        "workspace",
    )

    model = Org

    class Menu(MenuMixin, InferOrgMixin, SmartTemplateView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/((?P<submenu>[A-z]+)/)?$" % (path, action)

        def has_permission(self, request, *args, **kwargs):
            if self.request.user.is_staff:
                return True

            return super().has_permission(request, *args, **kwargs)

        def derive_menu(self):
            submenu = self.kwargs.get("submenu")
            org = self.request.org

            # how this menu is made up is a wip
            # TODO: remove pragma
            if submenu == "settings":  # pragma: no cover
                menu = [
                    self.create_menu_item(
                        menu_id="workspace", name=self.request.org.name, icon="settings", href="orgs.org_workspace"
                    )
                ]

                if self.has_org_perm("orgs.org_sub_orgs") and Org.FEATURE_CHILD_ORGS in org.features:
                    children = org.children.filter(is_active=True).count()
                    item = self.create_menu_item(name=_("Workspaces"), icon="children", href="orgs.org_sub_orgs")
                    if children:
                        item["count"] = children
                    menu.append(item)

                if self.has_org_perm("orgs.org_dashboard") and Org.FEATURE_CHILD_ORGS in org.features:
                    menu.append(
                        self.create_menu_item(
                            menu_id="dashboard",
                            name=_("Dashboard"),
                            icon="dashboard",
                            href="dashboard.dashboard_home",
                        )
                    )

                if self.request.user.is_authenticated:
                    menu.append(
                        self.create_menu_item(
                            menu_id="account",
                            name=_("Account"),
                            icon="account",
                            href=reverse("orgs.user_account"),
                        )
                    )

                if self.has_org_perm("orgs.org_manage_accounts") and Org.FEATURE_USERS in org.features:
                    menu.append(
                        self.create_menu_item(
                            name=_("Users"),
                            icon="users",
                            href="orgs.org_manage_accounts",
                            count=org.users.count(),
                        )
                    )

                menu.append(self.create_menu_item(name=_("Resthooks"), icon="resthooks", href="orgs.org_resthooks"))

                if self.has_org_perm("notifications.incident_list"):
                    menu.append(
                        self.create_menu_item(name=_("Incidents"), icon="incidents", href="notifications.incident_list")
                    )

                if self.has_org_perm("channels.channel_read"):
                    from temba.channels.views import get_channel_read_url

                    items = []
                    channels = org.channels.filter(is_active=True).order_by(Lower("name"))
                    for channel in channels:
                        items.append(
                            self.create_menu_item(
                                menu_id=str(channel.uuid),
                                name=channel.name,
                                href=get_channel_read_url(channel),
                                icon=channel.type.get_icon(),
                            )
                        )

                    if len(items):
                        menu.append(self.create_menu_item(name=_("Channels"), items=items, inline=True))

                if self.has_org_perm("classifiers.classifier_read"):
                    items = []
                    classifiers = org.classifiers.filter(is_active=True).order_by(Lower("name"))
                    for classifier in classifiers:
                        items.append(
                            self.create_menu_item(
                                menu_id=classifier.uuid,
                                name=classifier.name,
                                href=reverse("classifiers.classifier_read", args=[classifier.uuid]),
                                icon=classifier.get_type().get_icon(),
                            )
                        )

                    if len(items):
                        menu.append(self.create_menu_item(name=_("Classifiers"), items=items, inline=True))

                if self.has_org_perm("archives.archive_message"):
                    items = [
                        self.create_menu_item(
                            menu_id="message",
                            name=_("Messages"),
                            icon="message",
                            href=reverse("archives.archive_message"),
                        ),
                        self.create_menu_item(
                            menu_id="run",
                            name=_("Flow Runs"),
                            icon="flow",
                            href=reverse("archives.archive_run"),
                        ),
                    ]

                    menu.append(self.create_menu_item(name=_("Archives"), items=items, inline=True))

                return menu

            if submenu == "staff":
                return [
                    self.create_menu_item(
                        menu_id="workspaces",
                        name=_("Workspaces"),
                        icon="workspace",
                        href=reverse("orgs.org_manage"),
                    ),
                    self.create_menu_item(
                        menu_id="users",
                        name=_("Users"),
                        icon="users",
                        href=reverse("orgs.user_list"),
                    ),
                ]

            menu = []
            if org:
                other_orgs = User.get_orgs_for_request(self.request).exclude(id=org.id).order_by("-parent", "name")
                other_org_items = [
                    self.create_menu_item(menu_id=other_org.id, name=other_org.name, avatar=other_org.name, event=True)
                    for other_org in other_orgs
                ]

                if len(other_org_items):
                    other_org_items.insert(0, self.create_divider())

                if self.has_org_perm("orgs.org_create"):
                    if Org.FEATURE_NEW_ORGS in org.features and Org.FEATURE_CHILD_ORGS not in org.features:
                        other_org_items.append(self.create_divider())
                        other_org_items.append(
                            self.create_modax_button(name=_("New Workspace"), href="orgs.org_create")
                        )

                menu += [
                    self.create_menu_item(
                        menu_id="workspace",
                        name=_("Workspace"),
                        avatar=org.name,
                        popup=True,
                        items=[
                            self.create_space(),
                            self.create_menu_item(menu_id="settings", name=org.name, avatar=org.name, event=True),
                            self.create_divider(),
                            self.create_menu_item(
                                menu_id="logout",
                                name=_("Sign Out"),
                                icon="logout",
                                posterize=True,
                                href=f"{reverse('users.user_logout')}?next={reverse('users.user_login')}",
                            ),
                            *other_org_items,
                            self.create_space(),
                        ],
                    )
                ]

            menu += [
                self.create_space(),
                self.create_menu_item(
                    menu_id="msg",
                    name=_("Messages"),
                    icon="messages",
                    endpoint="msgs.msg_menu",
                    href="msgs.msg_inbox",
                    perm="msgs.msg_list",
                ),
                self.create_menu_item(
                    menu_id="contact",
                    name=_("Contacts"),
                    icon="contacts",
                    endpoint="contacts.contact_menu",
                    href="contacts.contact_list",
                    perm="contacts.contact_list",
                ),
                self.create_menu_item(
                    menu_id="flow",
                    name=_("Flows"),
                    icon="flows",
                    endpoint="flows.flow_menu",
                    href="flows.flow_list",
                    perm="flows.flow_list",
                ),
                self.create_menu_item(
                    menu_id="trigger",
                    name=_("Triggers"),
                    icon="triggers",
                    endpoint="triggers.trigger_menu",
                    href="triggers.trigger_list",
                    perm="triggers.trigger_list",
                ),
                self.create_menu_item(
                    menu_id="campaign",
                    name=_("Campaigns"),
                    icon="campaigns",
                    endpoint="campaigns.campaign_menu",
                    href="campaigns.campaign_list",
                    perm="campaigns.campaign_list",
                ),
                self.create_menu_item(
                    menu_id="ticket",
                    name=_("Tickets"),
                    icon="tickets",
                    endpoint="tickets.ticket_menu",
                    href="tickets.ticket_list",
                ),
            ]

            if org:
                unseen_bubble = None
                if self.request.user.notifications.filter(org=org, is_seen=False).exists():
                    unseen_bubble = "tomato"

                menu.append(
                    self.create_menu_item(
                        menu_id="notifications",
                        name=_("Notifications"),
                        icon="notification",
                        bottom=True,
                        popup=True,
                        bubble=unseen_bubble,
                        items=[
                            self.create_list(
                                "notifications", "/api/internal/notifications.json", "temba-notification-list"
                            )
                        ],
                    )
                )

            if not org or not self.has_org_perm("orgs.org_workspace"):
                settings_view = "orgs.user_account"
            else:
                settings_view = "orgs.org_workspace"

            menu.append(
                {
                    "id": "settings",
                    "name": _("Settings"),
                    "icon": "home",
                    "href": reverse(settings_view),
                    "endpoint": f"{reverse('orgs.org_menu')}settings/",
                    "bottom": True,
                    "show_header": True,
                }
            )

            if self.request.user.is_staff:
                menu.append(
                    self.create_menu_item(
                        menu_id="staff",
                        name=_("Staff"),
                        icon="staff",
                        endpoint=f"{reverse('orgs.org_menu')}staff/",
                        bottom=True,
                    )
                )

            return menu

            # Other Plugins:
            # Wit.ai, Luis, Bothub, ZenDesk, DT One, Chatbase, Prometheus, Zapier/Resthooks

    class Export(SpaMixin, InferOrgMixin, OrgPermsMixin, SmartTemplateView):
        title = _("Create Export")
        menu_path = "/settings/workspace"

        def post(self, request, *args, **kwargs):
            org = self.get_object()

            flow_ids = [elt for elt in self.request.POST.getlist("flows") if elt]
            campaign_ids = [elt for elt in self.request.POST.getlist("campaigns") if elt]

            # fetch the selected flows and campaigns
            flows = Flow.objects.filter(id__in=flow_ids, org=org, is_active=True)
            campaigns = Campaign.objects.filter(id__in=campaign_ids, org=org, is_active=True)

            components = set(itertools.chain(flows, campaigns))

            # add triggers for the selected flows
            for flow in flows:
                components.update(flow.triggers.filter(is_active=True, is_archived=False))

            export = org.export_definitions(f"https://{org.get_brand_domain()}", components)
            response = JsonResponse(export, json_dumps_params=dict(indent=2))
            response["Content-Disposition"] = "attachment; filename=%s.json" % slugify(org.name)
            return response

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.get_object()
            include_archived = bool(int(self.request.GET.get("archived", 0)))

            buckets, singles = self.generate_export_buckets(org, include_archived)

            context["archived"] = include_archived
            context["buckets"] = buckets
            context["singles"] = singles

            context["initial_flow_id"] = int(self.request.GET.get("flow", 0))
            context["initial_campaign_id"] = int(self.request.GET.get("campaign", 0))

            return context

        def generate_export_buckets(self, org, include_archived):
            """
            Generates a set of buckets of related exportable flows and campaigns
            """
            dependencies = org.generate_dependency_graph(include_archived=include_archived)

            unbucketed = set(dependencies.keys())
            buckets = []

            # helper method to add a component and its dependencies to a bucket
            def collect_component(c, bucket):
                if c in bucket:  # pragma: no cover
                    return

                unbucketed.remove(c)
                bucket.add(c)

                for d in dependencies[c]:
                    if d in unbucketed:
                        collect_component(d, bucket)

            while unbucketed:
                component = next(iter(unbucketed))

                bucket = set()
                buckets.append(bucket)

                collect_component(component, bucket)

            # collections with only one non-group component should be merged into a single "everything else" collection
            non_single_buckets = []
            singles = set()

            # items within buckets are sorted by type and name
            def sort_key(c):
                return c.__class__.__name__, c.name.lower()

            # buckets with a single item are merged into a special singles bucket
            for b in buckets:
                if len(b) > 1:
                    sorted_bucket = sorted(list(b), key=sort_key)
                    non_single_buckets.append(sorted_bucket)
                else:
                    singles.update(b)

            # put the buckets with the most items first
            non_single_buckets = sorted(non_single_buckets, key=lambda b: len(b), reverse=True)

            # sort singles
            singles = sorted(list(singles), key=sort_key)

            return non_single_buckets, singles

    class SmtpServer(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            from_email = forms.CharField(
                max_length=128,
                label=_("Email Address"),
                required=False,
                help_text=_("The from email address, can contain a name: ex: Jane Doe <jane@example.org>"),
                widget=InputWidget(),
            )
            smtp_host = forms.CharField(
                max_length=128,
                required=False,
                widget=InputWidget(attrs={"widget_only": True, "placeholder": _("SMTP Host")}),
            )
            smtp_username = forms.CharField(max_length=128, label=_("Username"), required=False, widget=InputWidget())
            smtp_password = forms.CharField(
                max_length=128,
                label=_("Password"),
                required=False,
                help_text=_("Leave blank to keep the existing set password if one exists"),
                widget=InputWidget(attrs={"password": True}),
            )
            smtp_port = forms.CharField(
                max_length=128,
                required=False,
                widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Port")}),
            )
            disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=True)

            def clean(self):
                super().clean()
                if self.cleaned_data.get("disconnect", "false") == "false":
                    from_email = self.cleaned_data.get("from_email", None)
                    smtp_host = self.cleaned_data.get("smtp_host", None)
                    smtp_username = self.cleaned_data.get("smtp_username", None)
                    smtp_password = self.cleaned_data.get("smtp_password", None)
                    smtp_port = self.cleaned_data.get("smtp_port", None)

                    config = self.instance.config
                    existing_smtp_server = urlparse(config.get("smtp_server", ""))
                    existing_username = ""
                    if existing_smtp_server.username:
                        existing_username = unquote(existing_smtp_server.username)
                    if not smtp_password and existing_username == smtp_username and existing_smtp_server.password:
                        smtp_password = unquote(existing_smtp_server.password)

                    if not from_email:
                        raise ValidationError(_("You must enter a from email"))

                    parsed = parseaddr(from_email)
                    if not is_valid_address(parsed[1]):
                        raise ValidationError(_("Please enter a valid email address"))

                    if not smtp_host:
                        raise ValidationError(_("You must enter the SMTP host"))

                    if not smtp_username:
                        raise ValidationError(_("You must enter the SMTP username"))

                    if not smtp_password:
                        raise ValidationError(_("You must enter the SMTP password"))

                    if not smtp_port:
                        raise ValidationError(_("You must enter the SMTP port"))

                    self.cleaned_data["smtp_password"] = smtp_password

                    try:
                        from temba.utils.email import send_custom_smtp_email

                        admin_emails = [admin.email for admin in self.instance.get_admins().order_by("email")]

                        branding = self.instance.branding
                        subject = _("%(name)s SMTP configuration test") % branding
                        body = (
                            _(
                                "This email is a test to confirm the custom SMTP server configuration added to your %(name)s account."
                            )
                            % branding
                        )

                        send_custom_smtp_email(
                            admin_emails,
                            subject,
                            body,
                            from_email,
                            smtp_host,
                            smtp_port,
                            smtp_username,
                            smtp_password,
                            use_tls=True,
                        )

                    except smtplib.SMTPException as e:
                        raise ValidationError(
                            _("Failed to send email with STMP server configuration with error '%s'") % str(e)
                        )
                    except Exception:
                        raise ValidationError(_("Failed to send email with STMP server configuration"))

                return self.cleaned_data

            class Meta:
                model = Org
                fields = ("from_email", "smtp_host", "smtp_username", "smtp_password", "smtp_port", "disconnect")

        form_class = Form
        success_message = ""

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.get_object()
            smtp_server = org.config.get(Org.CONFIG_SMTP_SERVER)
            parsed_smtp_server = urlparse(smtp_server)
            smtp_username = ""
            if parsed_smtp_server.username:
                smtp_username = unquote(parsed_smtp_server.username)
            smtp_password = ""
            if parsed_smtp_server.password:
                smtp_password = unquote(parsed_smtp_server.password)

            initial["from_email"] = parse_qs(parsed_smtp_server.query).get("from", [None])[0]
            initial["smtp_host"] = parsed_smtp_server.hostname
            initial["smtp_username"] = smtp_username
            initial["smtp_password"] = smtp_password
            initial["smtp_port"] = parsed_smtp_server.port
            initial["disconnect"] = "false"
            return initial

        def form_valid(self, form):
            disconnect = form.cleaned_data.get("disconnect", "false") == "true"
            user = self.request.user
            org = self.request.org

            if disconnect:
                org.remove_smtp_config(user)
                return HttpResponseRedirect(reverse("orgs.org_workspace"))
            else:
                smtp_from_email = form.cleaned_data["from_email"]
                smtp_host = form.cleaned_data["smtp_host"]
                smtp_username = form.cleaned_data["smtp_username"]
                smtp_password = form.cleaned_data["smtp_password"]
                smtp_port = form.cleaned_data["smtp_port"]

                org.add_smtp_config(smtp_from_email, smtp_host, smtp_username, smtp_password, smtp_port, user)

            return super().form_valid(form)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()
            from_email_custom = None

            if org.has_smtp_config():
                smtp_server = org.config.get(Org.CONFIG_SMTP_SERVER)
                parsed_smtp_server = urlparse(smtp_server)
                from_email_params = parse_qs(parsed_smtp_server.query).get("from")
                if from_email_params:
                    from_email_custom = parseaddr(from_email_params[0])[1]  # extract address only

            context["from_email_default"] = parseaddr(settings.FLOW_FROM_EMAIL)[1]
            context["from_email_custom"] = from_email_custom
            return context

    class Read(StaffOnlyMixin, SpaMixin, ContentMenuMixin, SmartReadView):
        def build_content_menu(self, menu):
            obj = self.get_object()
            if not obj.is_active:
                return

            menu.add_modax(
                _("Edit"),
                "update-workspace",
                reverse("orgs.org_update", args=[obj.id]),
                title=_("Edit Workspace"),
                as_button=True,
                on_submit="handleWorkspaceUpdated()",
            )

            if not obj.is_flagged:
                menu.add_url_post(_("Flag"), f"{reverse('orgs.org_update', args=[obj.id])}?action=flag")
            else:
                menu.add_url_post(_("Unflag"), f"{reverse('orgs.org_update', args=[obj.id])}?action=unflag")

            if not obj.is_child:
                if not obj.is_suspended:
                    menu.add_url_post(_("Suspend"), f"{reverse('orgs.org_update', args=[obj.id])}?action=suspend")
                else:
                    menu.add_url_post(_("Unsuspend"), f"{reverse('orgs.org_update', args=[obj.id])}?action=unsuspend")

            if not obj.is_verified:
                menu.add_url_post(_("Verify"), f"{reverse('orgs.org_update', args=[obj.id])}?action=verify")

            menu.new_group()
            menu.add_url_post(
                _("Service"),
                f'{reverse("orgs.org_service")}?other_org={obj.id}&next={reverse("msgs.msg_inbox", args=[])}',
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

    class Manage(StaffOnlyMixin, SpaMixin, SmartListView):
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
            return f"/staff/{self.request.GET.get('filter', 'all')}"

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
                return reverse("orgs.user_update", args=[owner.pk])
            return super().lookup_field_link(context, field, obj)

    class Update(StaffOnlyMixin, SpaMixin, ModalMixin, ComponentFormMixin, SmartUpdateView):
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
        success_url = "hide"

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

                return HttpResponseRedirect(reverse("orgs.org_read", args=[obj.id]))

            return super().post(request, *args, **kwargs)

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            cleaned_data = self.form.cleaned_data

            obj.limits = cleaned_data["limits"]
            return obj

    class DeleteChild(SpaMixin, OrgObjPermsMixin, ModalMixin, SmartDeleteView):
        cancel_url = "@orgs.org_sub_orgs"
        success_url = "@orgs.org_sub_orgs"
        fields = ("id",)
        submit_button_name = _("Delete")

        def get_object_org(self):
            # child orgs work in the context of their parent
            org = self.get_object()
            return org if not org.is_child else org.parent

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["delete_on"] = timezone.now() + timedelta(days=Org.DELETE_DELAY_DAYS)
            return context

        def post(self, request, *args, **kwargs):
            assert self.get_object().is_child, "can only delete child orgs"

            self.object = self.get_object()
            self.object.release(request.user)
            return self.render_modal_response()

    class ManageAccounts(SpaMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class AccountsForm(forms.ModelForm):
            invite_emails = forms.CharField(
                required=False, widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Email Address")})
            )
            invite_role = forms.ChoiceField(
                choices=[], required=True, initial="V", label=_("Role"), widget=SelectWidget()
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                role_choices = [(r.code, r.display) for r in org.get_allowed_user_roles()]

                self.fields["invite_role"].choices = role_choices

                self.org = org
                self.user_rows = []
                self.invite_rows = []
                self.add_per_user_fields(org, role_choices)
                self.add_per_invite_fields(org)

            def add_per_user_fields(self, org: Org, role_choices: list):
                for user in org.users.order_by("email"):
                    role_field = forms.ChoiceField(
                        choices=role_choices,
                        required=True,
                        initial=org.get_user_role(user).code,
                        label=" ",
                        widget=SelectWidget(),
                    )
                    remove_field = forms.BooleanField(
                        required=False, label=" ", widget=CheckboxWidget(attrs={"widget_only": True})
                    )

                    self.fields.update(
                        OrderedDict([(f"user_{user.id}_role", role_field), (f"user_{user.id}_remove", remove_field)])
                    )
                    self.user_rows.append(
                        {"user": user, "role_field": f"user_{user.id}_role", "remove_field": f"user_{user.id}_remove"}
                    )

            def add_per_invite_fields(self, org: Org):
                for invite in org.invitations.filter(is_active=True).order_by("email"):
                    role_field = forms.ChoiceField(
                        choices=[(r.code, r.display) for r in OrgRole],
                        required=True,
                        initial=invite.role.code,
                        label=" ",
                        widget=SelectWidget(),
                        disabled=True,
                    )
                    remove_field = forms.BooleanField(
                        required=False, label=" ", widget=CheckboxWidget(attrs={"widget_only": True})
                    )

                    self.fields.update(
                        OrderedDict(
                            [(f"invite_{invite.id}_role", role_field), (f"invite_{invite.id}_remove", remove_field)]
                        )
                    )
                    self.invite_rows.append(
                        {
                            "invite": invite,
                            "role_field": f"invite_{invite.id}_role",
                            "remove_field": f"invite_{invite.id}_remove",
                        }
                    )

            def clean_invite_emails(self):
                emails = self.cleaned_data["invite_emails"].lower().strip()
                existing_users_emails = set(
                    list(self.org.users.values_list("username", flat=True))
                    + list(self.org.invitations.filter(is_active=True).values_list("email", flat=True))
                )
                cleaned_emails = []
                if emails:
                    email_list = emails.split(",")
                    for email in email_list:
                        email = email.strip()
                        try:
                            validate_email(email)
                        except ValidationError:
                            raise forms.ValidationError(_("One of the emails you entered is invalid."))

                        if email in existing_users_emails:
                            raise forms.ValidationError(
                                _("One of the emails you entered has an existing user on the workspace.")
                            )

                        if email in cleaned_emails:
                            raise forms.ValidationError(_("One of the emails you entered is duplicated."))

                        cleaned_emails.append(email)

                return ",".join(cleaned_emails)

            def get_submitted_roles(self) -> dict:
                """
                Returns a dict of users to roles from the current form data. None role means removal.
                """
                roles = {}

                for row in self.user_rows:
                    role = self.cleaned_data.get(row["role_field"])
                    remove = self.cleaned_data.get(row["remove_field"])
                    roles[row["user"]] = OrgRole.from_code(role) if not remove else None
                return roles

            def get_submitted_invite_removals(self) -> list:
                """
                Returns a list of invites to be removed.
                """
                invites = []
                for row in self.invite_rows:
                    if self.cleaned_data[row["remove_field"]]:
                        invites.append(row["invite"])
                return invites

            def clean(self):
                super().clean()

                new_roles = self.get_submitted_roles()
                has_admin = False
                for new_role in new_roles.values():
                    if new_role == OrgRole.ADMINISTRATOR:
                        has_admin = True
                        break

                if not has_admin:
                    raise forms.ValidationError(_("A workspace must have at least one administrator."))

            class Meta:
                model = Invitation
                fields = ("invite_emails", "invite_role")

        form_class = AccountsForm
        success_url = "@orgs.org_manage_accounts"
        success_message = ""
        submit_button_name = _("Save Changes")
        title = _("Users")
        menu_path = "/settings/users"

        def pre_process(self, request, *args, **kwargs):
            if Org.FEATURE_USERS not in request.org.features:
                return HttpResponseRedirect(reverse("orgs.org_workspace"))

        def derive_title(self):
            if self.object.is_child:
                return self.object.name
            else:
                return super().derive_title()

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.get_object()
            return kwargs

        def post_save(self, obj):
            obj = super().post_save(obj)

            cleaned_data = self.form.cleaned_data
            org = self.get_object()
            allowed_roles = org.get_allowed_user_roles()

            # delete any invitations which have been checked for removal
            for invite in self.form.get_submitted_invite_removals():
                org.invitations.filter(id=invite.id).delete()

            # handle any requests for new invitations
            invite_emails = cleaned_data["invite_emails"]
            if invite_emails:
                invite_role = OrgRole.from_code(cleaned_data["invite_role"])
                Invitation.bulk_create_or_update(org, self.request.user, invite_emails.split(","), invite_role)

            # update org users with new roles
            for user, new_role in self.form.get_submitted_roles().items():
                if not new_role:
                    org.remove_user(user)
                elif org.get_user_role(user) != new_role and new_role in allowed_roles:
                    org.add_user(user, new_role)

                # when a user's role changes, delete any API tokens they're no longer allowed to have
                for token in APIToken.objects.filter(org=org, user=user):
                    if not token.is_valid():
                        token.release()

            return obj

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()
            context["org"] = org
            context["has_invites"] = org.invitations.filter(is_active=True).exists()
            return context

        def get_success_url(self):
            still_in_org = self.get_object().has_user(self.request.user) or self.request.user.is_staff

            # if current user no longer belongs to this org, redirect to org chooser
            return reverse("orgs.org_manage_accounts") if still_in_org else reverse("orgs.org_choose")

    class ManageAccountsSubOrg(ManageAccounts):
        menu_path = "/settings/workspaces"

        def pre_process(self, request, *args, **kwargs):
            pass

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org_id = self.request.GET.get("org")
            context["parent"] = Org.objects.filter(id=org_id, parent=self.request.org).first()
            return context

        def get_object(self, *args, **kwargs):
            org_id = self.request.GET.get("org")
            return Org.objects.filter(id=org_id, parent=self.request.org).first()

        def get_success_url(self):  # pragma: needs cover
            org_id = self.request.GET.get("org")
            return "%s?org=%s" % (reverse("orgs.org_manage_accounts_sub_org"), org_id)

    class Service(StaffOnlyMixin, SmartFormView):
        class ServiceForm(forms.Form):
            other_org = ModelChoiceField(queryset=Org.objects.all(), widget=forms.HiddenInput())
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
            switch_to_org(self.request, form.cleaned_data["other_org"])
            success_url = form.cleaned_data["next"] or reverse("msgs.msg_inbox")
            return HttpResponseRedirect(success_url)

        # invalid form login 'logs out' the user from the org and takes them to the org manage page
        def form_invalid(self, form):
            switch_to_org(self.request, None)
            return HttpResponseRedirect(reverse("orgs.org_manage"))

    class SubOrgs(SpaMixin, ContentMenuMixin, OrgPermsMixin, InferOrgMixin, SmartListView):
        title = _("Workspaces")
        menu_path = "/settings/workspaces"

        def build_content_menu(self, menu):
            org = self.get_object()

            enabled = Org.FEATURE_CHILD_ORGS in org.features or Org.FEATURE_NEW_ORGS in org.features
            if self.has_org_perm("orgs.org_create") and enabled:
                menu.add_modax(_("New Workspace"), "new_workspace", reverse("orgs.org_create"))

        def derive_queryset(self, **kwargs):
            queryset = super().derive_queryset(**kwargs)

            # all our children
            org = self.get_object()
            ids = [child.id for child in Org.objects.filter(parent=org)]

            return queryset.filter(id__in=ids, is_active=True).order_by("-parent", "name")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()
            if self.has_org_perm("orgs.org_manage_accounts") and Org.FEATURE_USERS in org.features:
                context["manage_users"] = True

            return context

    class Create(NonAtomicMixin, SpaMixin, OrgPermsMixin, ModalMixin, InferOrgMixin, SmartCreateView):
        class Form(forms.ModelForm):
            TYPE_CHILD = "child"
            TYPE_NEW = "new"
            TYPE_CHOICES = ((TYPE_CHILD, _("As child workspace")), (TYPE_NEW, _("As separate workspace")))

            type = forms.ChoiceField(initial=TYPE_CHILD, widget=SelectWidget(attrs={"widget_only": True}))
            name = forms.CharField(label=_("Name"), widget=InputWidget())
            timezone = TimeZoneFormField(widget=SelectWidget(attrs={"searchable": True}))

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["type"].choices = self.TYPE_CHOICES
                self.fields["timezone"].initial = org.timezone

            class Meta:
                model = Org
                fields = ("type", "name", "timezone")

        form_class = Form

        def pre_process(self, request, *args, **kwargs):
            # if org has neither feature then redirect
            features = self.request.org.features
            if Org.FEATURE_NEW_ORGS not in features and Org.FEATURE_CHILD_ORGS not in features:
                return HttpResponseRedirect(reverse("orgs.org_workspace"))

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def derive_fields(self):
            # if org supports creating both new and child orgs, need to show type as option
            features = self.request.org.features
            show_type = Org.FEATURE_NEW_ORGS in features and Org.FEATURE_CHILD_ORGS in features
            return ["type", "name", "timezone"] if show_type else ["name", "timezone"]

        def get_success_url(self):
            # if we created a child org, redirect to its management
            if self.object.is_child:
                return reverse("orgs.org_sub_orgs")

            # if we created a new separate org, switch to it
            switch_to_org(self.request, self.object)
            return reverse("orgs.org_start")

        def form_valid(self, form):
            default_type = form.TYPE_CHILD if Org.FEATURE_CHILD_ORGS in self.request.org.features else form.TYPE_NEW

            self.object = self.request.org.create_new(
                self.request.user,
                form.cleaned_data["name"],
                tz=form.cleaned_data["timezone"],
                as_child=form.cleaned_data.get("type", default_type) == form.TYPE_CHILD,
            )

            if "HTTP_X_PJAX" not in self.request.META:
                return HttpResponseRedirect(self.get_success_url())
            else:  # pragma: no cover
                success_url = self.get_success_url()

                response = self.render_to_response(
                    self.get_context_data(
                        form=form,
                        success_url=success_url,
                        success_script=getattr(self, "success_script", None),
                    )
                )

                response["Temba-Success"] = success_url
                return response

    class Start(SmartTemplateView):
        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def pre_process(self, request, *args, **kwargs):
            user = self.request.user
            org = self.request.org

            if not org:
                if user.is_staff:
                    return HttpResponseRedirect(reverse("orgs.org_manage"))

                return HttpResponseRedirect(reverse("orgs.org_choose"))

            role = org.get_user_role(user)
            return HttpResponseRedirect(reverse(role.start_view))

    class Choose(NoNavMixin, SpaMixin, SmartFormView):
        class Form(forms.Form):
            organization = forms.ModelChoiceField(queryset=Org.objects.none(), empty_label=None)

            def __init__(self, orgs, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["organization"].queryset = orgs

        form_class = Form
        fields = ("organization",)
        title = _("Select your Workspace")

        def pre_process(self, request, *args, **kwargs):
            user = self.request.user
            if user.is_authenticated:
                user_orgs = User.get_orgs_for_request(self.request)
                if user_orgs.count() == 1:
                    org = user_orgs[0]
                    switch_to_org(self.request, org)
                    analytics.identify(user, self.request.branding, org)

                    return HttpResponseRedirect(reverse("orgs.org_start"))

                elif user_orgs.count() == 0:
                    if user.is_staff:
                        return HttpResponseRedirect(reverse("orgs.org_manage"))

                    # for regular users, if there's no orgs, log them out with a message
                    messages.info(request, _("No workspaces for this account, please contact your administrator."))
                    logout(request)
                    return HttpResponseRedirect(reverse("users.user_login"))
            return None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["orgs"] = User.get_orgs_for_request(self.request)
            return context

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["orgs"] = User.get_orgs_for_request(self.request)
            return kwargs

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def form_valid(self, form):
            org = form.cleaned_data["organization"]
            switch_to_org(self.request, org)
            analytics.identify(self.request.user, self.request.branding, org)

            return HttpResponseRedirect(reverse("orgs.org_start"))

    class Join(NoNavMixin, InvitationMixin, SmartTemplateView):
        """
        Invitation emails link here allowing users to join workspaces.
        """

        permission = False

        def pre_process(self, request, *args, **kwargs):
            resp = super().pre_process(request, *args, **kwargs)
            if resp:
                return resp

            secret = self.kwargs["secret"]

            # if user exists and is logged in then they just need to accept
            user_exists = User.objects.filter(username=self.invitation.email).exists()
            if user_exists and self.invitation.email == request.user.username:
                return HttpResponseRedirect(reverse("orgs.org_join_accept", args=[secret]))

            logout(request)

            if not user_exists:
                return HttpResponseRedirect(reverse("orgs.org_join_signup", args=[secret]))

    class JoinSignup(NoNavMixin, InvitationMixin, SmartUpdateView):
        """
        Sign up form for new users to accept a workspace invitations.
        """

        form_class = OrgSignupForm
        fields = ("first_name", "last_name", "password")
        success_message = ""
        success_url = "@orgs.org_start"
        submit_button_name = _("Sign Up")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            resp = super().pre_process(request, *args, **kwargs)
            if resp:
                return resp

            # if user already exists, we shouldn't be here
            if User.objects.filter(username=self.invitation.email).exists():
                return HttpResponseRedirect(reverse("orgs.org_join", args=[self.kwargs["secret"]]))

            return None

        def save(self, obj):
            user = User.create(
                self.invitation.email,
                self.form.cleaned_data["first_name"],
                self.form.cleaned_data["last_name"],
                password=self.form.cleaned_data["password"],
                language=obj.language,
            )

            # log the user in
            user = authenticate(username=user.username, password=self.form.cleaned_data["password"])
            login(self.request, user)

            obj.add_user(user, self.invitation.role)

            self.invitation.release()

    class JoinAccept(NoNavMixin, InvitationMixin, SmartUpdateView):
        """
        Simple join button for existing and logged in users to accept a workspace invitation.
        """

        class Form(forms.ModelForm):
            class Meta:
                model = Org
                fields = ()

        success_message = ""
        title = ""
        form_class = Form
        success_url = "@orgs.org_start"
        submit_button_name = _("Join")

        def has_permission(self, request, *args, **kwargs):
            return request.user.is_authenticated

        def pre_process(self, request, *args, **kwargs):
            resp = super().pre_process(request, *args, **kwargs)
            if resp:
                return resp

            # if user doesn't already exist or we're logged in as a different user, we shouldn't be here
            user_exists = User.objects.filter(username=self.invitation.email).exists()
            if not user_exists or self.invitation.email != request.user.username:
                return HttpResponseRedirect(reverse("orgs.org_join", args=[self.kwargs["secret"]]))

            return None

        def save(self, obj):
            obj.add_user(self.request.user, self.invitation.role)

            self.invitation.release()

            switch_to_org(self.request, obj)

    class Surveyor(SmartFormView):
        class PasswordForm(forms.Form):
            surveyor_password = forms.CharField(widget=forms.PasswordInput(attrs={"placeholder": "Password"}))

            def clean_surveyor_password(self):
                password = self.cleaned_data["surveyor_password"]
                org = Org.objects.filter(surveyor_password=password).first()
                if not org:
                    raise forms.ValidationError(
                        _("Invalid surveyor password, please check with your project leader and try again.")
                    )
                self.cleaned_data["org"] = org
                return password

        class RegisterForm(PasswordForm):
            surveyor_password = forms.CharField(widget=forms.HiddenInput())
            first_name = forms.CharField(
                help_text=_("Your first name"), widget=forms.TextInput(attrs={"placeholder": "First Name"})
            )
            last_name = forms.CharField(
                help_text=_("Your last name"), widget=forms.TextInput(attrs={"placeholder": "Last Name"})
            )
            email = forms.EmailField(
                help_text=_("Your email address"), widget=forms.TextInput(attrs={"placeholder": "Email"})
            )
            password = forms.CharField(
                widget=forms.PasswordInput(attrs={"placeholder": "Password"}),
                required=True,
                validators=[validate_password],
                help_text=_("Your password, at least eight letters please"),
            )

            def __init__(self, *args, **kwargs):
                super().__init__(*args, **kwargs)

            def clean_email(self):
                email = self.cleaned_data["email"]
                if email:
                    if User.objects.filter(username__iexact=email):
                        raise forms.ValidationError(_("That email address is already used"))

                return email.lower()

        permission = None
        form_class = PasswordForm

        def derive_initial(self):
            initial = super().derive_initial()
            initial["surveyor_password"] = self.request.POST.get("surveyor_password", "")
            return initial

        def get_context_data(self, **kwargs):
            context = super().get_context_data()
            context["form"] = self.form
            context["step"] = self.get_step()

            for key, field in self.form.fields.items():
                context[key] = field

            return context

        def get_success_url(self):
            return reverse("orgs.org_surveyor")

        def get_form_class(self):
            if self.get_step() == 2:
                return OrgCRUDL.Surveyor.RegisterForm
            else:
                return OrgCRUDL.Surveyor.PasswordForm

        def get_step(self):
            return 2 if "first_name" in self.request.POST else 1

        def form_valid(self, form):
            if self.get_step() == 1:
                org = self.form.cleaned_data.get("org", None)

                context = self.get_context_data()
                context["step"] = 2
                context["org"] = org

                self.form = OrgCRUDL.Surveyor.RegisterForm(initial=self.derive_initial())
                context["form"] = self.form

                return self.render_to_response(context)
            else:
                org = self.form.cleaned_data["org"]

                # create our user
                user = User.create(
                    self.form.cleaned_data["email"],
                    self.form.cleaned_data["first_name"],
                    self.form.cleaned_data["last_name"],
                    password=self.form.cleaned_data["password"],
                    language=org.language,
                )

                # log the user in
                user = authenticate(username=user.username, password=self.form.cleaned_data["password"])
                login(self.request, user)

                org.add_user(user, OrgRole.SURVEYOR)

                token = APIToken.get_or_create(org, user, role=OrgRole.SURVEYOR)
                org_name = quote(org.name)

                return HttpResponseRedirect(
                    f"{self.get_success_url()}?org={org_name}&uuid={str(org.uuid)}&token={token}&user={user.email}"
                )

        def form_invalid(self, form):
            return super().form_invalid(form)

        def derive_title(self):
            return _("Welcome!")

        def get_template_names(self):
            if (
                "android" in self.request.META.get("HTTP_X_REQUESTED_WITH", "")
                or "mobile" in self.request.GET
                or "Android" in self.request.META.get("HTTP_USER_AGENT", "")
            ):
                return ["orgs/org_surveyor_mobile.html"]
            else:
                return super().get_template_names()

    class Grant(NonAtomicMixin, SmartCreateView):
        title = _("Create Workspace Account")
        form_class = OrgGrantForm
        success_message = "Workspace successfully created."
        submit_button_name = _("Create")
        success_url = "@orgs.org_grant"

        def save(self, obj):
            self.object = Org.create(
                self.request.user, self.form.cleaned_data["name"], self.form.cleaned_data["timezone"]
            )

            user = User.get_or_create(
                self.form.cleaned_data["email"],
                self.form.cleaned_data["first_name"],
                self.form.cleaned_data["last_name"],
                self.form.cleaned_data["password"],
                language=settings.DEFAULT_LANGUAGE,
            )
            self.object.add_user(user, OrgRole.ADMINISTRATOR)
            return self.object

    class Signup(ComponentFormMixin, NonAtomicMixin, SmartCreateView):
        title = _("Sign Up")
        form_class = OrgSignupForm
        permission = None
        success_message = ""
        submit_button_name = _("Save")

        def get_success_url(self):
            return "%s?start" % reverse("public.public_welcome")

        def pre_process(self, request, *args, **kwargs):
            # if our brand doesn't allow signups, then redirect to the homepage
            if "signups" not in request.branding.get("features", []):  # pragma: needs cover
                return HttpResponseRedirect(reverse("public.public_index"))

            else:
                return super().pre_process(request, *args, **kwargs)

        def derive_initial(self):
            initial = super().get_initial()
            initial["email"] = self.request.POST.get("email", self.request.GET.get("email", None))
            return initial

        def save(self, obj):
            new_user = User.create(
                self.form.cleaned_data["email"],
                self.form.cleaned_data["first_name"],
                self.form.cleaned_data["last_name"],
                self.form.cleaned_data["password"],
                language=settings.DEFAULT_LANGUAGE,
            )

            self.object = Org.create(new_user, self.form.cleaned_data["name"], self.form.cleaned_data["timezone"])

            analytics.identify(new_user, brand=self.request.branding, org=obj)
            analytics.track(new_user, "temba.org_signup", properties=dict(org=self.object.name))

            switch_to_org(self.request, obj)
            login(self.request, new_user)
            return obj

    class Resthooks(SpaMixin, ComponentFormMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class ResthookForm(forms.ModelForm):
            new_slug = forms.SlugField(
                required=False,
                label=_("New Event"),
                help_text="Enter a name for your event. ex: new-registration",
                widget=InputWidget(),
                max_length=Resthook._meta.get_field("slug").max_length,
            )

            def add_remove_fields(self):
                resthooks = []
                field_mapping = []

                for resthook in self.instance.get_resthooks():
                    check_field = forms.BooleanField(required=False, widget=CheckboxWidget())
                    field_name = "resthook_%d" % resthook.id

                    field_mapping.append((field_name, check_field))
                    resthooks.append(dict(resthook=resthook, field=field_name))

                self.fields = OrderedDict(list(self.fields.items()) + field_mapping)
                return resthooks

            def clean_new_slug(self):
                new_slug = self.data.get("new_slug")

                if new_slug:
                    if self.instance.resthooks.filter(is_active=True, slug__iexact=new_slug):
                        raise ValidationError("This event name has already been used.")

                return new_slug

            class Meta:
                model = Org
                fields = ("id", "new_slug")

        form_class = ResthookForm
        success_message = ""
        title = _("Resthooks")
        success_url = "@orgs.org_resthooks"
        menu_path = "/settings/resthooks"

        def get_form(self):
            form = super().get_form()
            self.current_resthooks = form.add_remove_fields()
            return form

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["current_resthooks"] = self.current_resthooks
            return context

        def pre_save(self, obj):
            new_slug = self.form.data.get("new_slug")
            if new_slug:
                Resthook.get_or_create(obj, new_slug, self.request.user)

            # release any resthooks that the user removed
            for resthook in self.current_resthooks:
                if self.form.data.get(resthook["field"]):
                    resthook["resthook"].release(self.request.user)

            return super().pre_save(obj)

    class Prometheus(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class ToggleForm(forms.ModelForm):
            class Meta:
                model = Org
                fields = ("id",)

        form_class = ToggleForm
        success_url = "@orgs.org_workspace"
        success_message = ""

        def post_save(self, obj):
            # if org has an existing Prometheus token, disable it, otherwise create one
            org = self.request.org
            existing = self.get_token(org)
            if existing:
                existing.release()
            else:
                APIToken.get_or_create(self.request.org, self.request.user, prometheus=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.request.org
            token = self.get_token(org)
            if token:
                context["prometheus_token"] = token.key
                context["prometheus_url"] = f"https://{org.branding['domain']}/mr/org/{org.uuid}/metrics"

            return context

        def get_token(self, org):
            return APIToken.objects.filter(is_active=True, org=org, role=Group.objects.get(name="Prometheus")).first()

    class Workspace(SpaMixin, FormaxMixin, ContentMenuMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Workspace")
        menu_path = "/settings/workspace"

        def build_content_menu(self, menu):
            menu.add_link(_("New Channel"), reverse("channels.channel_claim"), as_button=True)

            if self.has_org_perm("classifiers.classifier_connect"):
                menu.add_link(_("New Classifier"), reverse("classifiers.classifier_connect"))

            menu.new_group()

            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export"), reverse("orgs.org_export"))

            if self.has_org_perm("orgs.orgimport_create"):
                menu.add_link(_("Import"), reverse("orgs.orgimport_create"))

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("orgs.org_edit"):
                formax.add_section("org", reverse("orgs.org_edit"), icon="settings")

            if self.has_org_perm("orgs.org_languages"):
                formax.add_section("languages", reverse("orgs.org_languages"), icon="language")

            if self.has_org_perm("orgs.org_country") and "locations" in settings.FEATURES:
                formax.add_section("country", reverse("orgs.org_country"), icon="location")

            if self.has_org_perm("orgs.org_smtp_server"):
                formax.add_section("email", reverse("orgs.org_smtp_server"), icon="email")

            if self.has_org_perm("orgs.org_prometheus"):
                formax.add_section("prometheus", reverse("orgs.org_prometheus"), icon="prometheus", nobutton=True)

            if self.has_org_perm("orgs.org_manage_integrations"):
                for integration in IntegrationType.get_all():
                    if integration.is_available_to(self.request.user):
                        integration.management_ui(self.object, formax)

    class Edit(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            name = forms.CharField(max_length=128, label=_("Workspace Name"), help_text="", widget=InputWidget())
            timezone = TimeZoneFormField(
                label=_("Timezone"), help_text="", widget=SelectWidget(attrs={"searchable": True})
            )

            class Meta:
                model = Org
                fields = ("name", "timezone", "date_format", "language")
                widgets = {"date_format": SelectWidget(), "language": SelectWidget()}

        success_message = ""
        form_class = Form

    class EditSubOrg(SpaMixin, ModalMixin, Edit):
        success_url = "@orgs.org_sub_orgs"

        def get_success_url(self):
            return super().get_success_url()

        def get_object(self, *args, **kwargs):
            try:
                return self.request.org.children.get(id=int(self.request.GET.get("org")))
            except Org.DoesNotExist:
                raise Http404(_("No such child workspace"))

    class Country(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class CountryForm(forms.ModelForm):
            country = forms.ModelChoiceField(
                Org.get_possible_countries(),
                required=False,
                label=_("The country used for location values. (optional)"),
                help_text="State and district names will be searched against this country.",
                widget=SelectWidget(),
            )

            class Meta:
                model = Org
                fields = ("country",)

        success_message = ""
        form_class = CountryForm

    class Languages(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            primary_lang = ArbitraryJsonChoiceField(
                required=True,
                label=_("Default Flow Language"),
                help_text=_("Used for contacts with no language preference."),
                widget=SelectWidget(
                    attrs={
                        "placeholder": _("Select a language"),
                        "searchable": True,
                        "queryParam": "q",
                        "endpoint": reverse_lazy("orgs.org_languages"),
                    }
                ),
            )
            other_langs = ArbitraryJsonChoiceField(
                required=False,
                label=_("Additional Languages"),
                help_text=_("The languages that your flows can be translated into."),
                widget=SelectMultipleWidget(
                    attrs={
                        "placeholder": _("Select languages"),
                        "searchable": True,
                        "queryParam": "q",
                        "endpoint": reverse_lazy("orgs.org_languages"),
                    }
                ),
            )

            input_collation = forms.ChoiceField(
                required=True,
                choices=Org.COLLATION_CHOICES,
                label=_("Input Matching"),
                help_text=_("How text is matched against trigger keywords and flow split tests."),
                widget=SelectWidget(),
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.org = org

            class Meta:
                model = Org
                fields = ("primary_lang", "other_langs", "input_collation")

        success_message = ""
        form_class = Form

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.get_object()

            def lang_json(code):
                return {"value": code, "name": languages.get_name(code)}

            non_primary_langs = org.flow_languages[1:] if len(org.flow_languages) > 1 else []
            initial["other_langs"] = [lang_json(ln) for ln in non_primary_langs]
            initial["primary_lang"] = [lang_json(org.flow_languages[0])]
            return initial

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()

            primary_lang = languages.get_name(org.flow_languages[0])
            other_langs = sorted([languages.get_name(code) for code in org.flow_languages[1:]])

            context["primary_lang"] = primary_lang
            context["other_langs"] = other_langs
            return context

        def get(self, request, *args, **kwargs):
            if self.request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest":
                initial = self.request.GET.get("initial", "").split(",")
                matches = []

                if len(initial) > 0:
                    for iso_code in initial:
                        if iso_code:
                            lang = languages.get_name(iso_code)
                            matches.append({"value": iso_code, "name": lang})

                if len(matches) == 0:
                    search = self.request.GET.get("search", self.request.GET.get("q", "")).strip().lower()
                    matches += languages.search_by_name(search)
                return JsonResponse(dict(results=matches))

            return super().get(request, *args, **kwargs)

        def form_valid(self, form):
            user = self.request.user
            codes = [form.cleaned_data["primary_lang"]["value"]]

            for lang in form.cleaned_data["other_langs"]:
                if lang["value"] and lang["value"] not in codes:
                    codes.append(lang["value"])

            self.object.set_flow_languages(user, codes)

            return super().form_valid(form)

        def has_permission(self, request, *args, **kwargs):
            perm = "orgs.org_country"

            if self.request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest" and self.request.method == "GET":
                perm = "orgs.org_languages"

            return self.request.user.has_perm(perm) or self.has_org_perm(perm)


class OrgImportCRUDL(SmartCRUDL):
    model = OrgImport
    actions = ("create", "read")

    class Create(SpaMixin, OrgPermsMixin, SmartCreateView):
        menu_path = "/settings/workspace"

        class Form(forms.ModelForm):
            file = forms.FileField(help_text=_("The import file"))

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.org = org

            def clean_file(self):
                # check that it isn't too old
                data = self.cleaned_data["file"].read()
                try:
                    json_data = json.loads(force_str(data))
                except (DjangoUnicodeDecodeError, ValueError):
                    raise ValidationError(_("This file is not a valid flow definition file."))

                if Version(str(json_data.get("version", 0))) < Version(Org.EARLIEST_IMPORT_VERSION):
                    raise ValidationError(_("This file is no longer valid. Please export a new version and try again."))

                return self.cleaned_data["file"]

            class Meta:
                model = OrgImport
                fields = ("file",)

        success_message = _("Import started")
        success_url = "id@orgs.orgimport_read"
        form_class = Form

        def derive_title(self):
            return _("Import Flows")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            obj.org = self.request.org
            return obj

        def post_save(self, obj):
            obj.start_async()
            return obj

    class Read(SpaMixin, OrgPermsMixin, SmartReadView):
        menu_path = "/settings/workspace"

        def derive_title(self):
            return _("Import Flows and Campaigns")


class ExportCRUDL(SmartCRUDL):
    model = Export
    actions = ("download",)

    class Download(SpaMixin, NotificationTargetMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        menu_path = "/settings/workspace"
        title = _("Download Export")

        def get(self, request, *args, **kwargs):
            if str_to_bool(request.GET.get("raw", 0)):
                export = self.get_object()

                url, filename, mime_type = export.get_raw_access()

                if url.startswith("http"):  # pragma: needs cover
                    response = HttpResponseRedirect(url)
                else:
                    asset_file = open("." + url, "rb")
                    response = HttpResponse(asset_file, content_type=mime_type)
                    response["Content-Disposition"] = "attachment; filename=%s" % filename

                return response

            return super().get(request, *args, **kwargs)

        def get_notification_scope(self) -> tuple[str, str]:
            return "export:finished", self.get_object().get_notification_scope()
