from collections import OrderedDict
from datetime import timedelta
from urllib.parse import quote, quote_plus

import iso8601
import pyotp
from django_redis import get_redis_connection
from packaging.version import Version
from smartmin.users.models import FailedLogin, PasswordHistory, RecoveryToken
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
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.models import Group
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView as AuthLoginView
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.db.models.functions import Lower
from django.forms import ModelChoiceField
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404, resolve_url
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import DjangoUnicodeDecodeError, force_str
from django.utils.functional import cached_property
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.api.models import APIToken, Resthook
from temba.campaigns.models import Campaign
from temba.contacts.models import ContactField, ContactGroup
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.notifications.mixins import NotificationTargetMixin
from temba.orgs.tasks import send_user_verification_email
from temba.utils import analytics, get_anonymous_user, json, languages, on_transaction_commit, str_to_bool
from temba.utils.email import EmailSender, parse_smtp_url
from temba.utils.fields import (
    ArbitraryJsonChoiceField,
    CheckboxWidget,
    ImagePickerWidget,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaDateField,
)
from temba.utils.text import generate_secret
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

from .forms import SignupForm, SMTPForm
from .models import (
    BackupToken,
    DefinitionExport,
    Export,
    IntegrationType,
    Invitation,
    Org,
    OrgImport,
    OrgRole,
    User,
    UserSettings,
)

# session key for storing a two-factor enabled user's id once we've checked their password
TWO_FACTOR_USER_SESSION_KEY = "_two_factor_user_id"
TWO_FACTOR_STARTED_SESSION_KEY = "_two_factor_started_on"
TWO_FACTOR_LIMIT_SECONDS = 5 * 60


def switch_to_org(request, org, *, servicing: bool = False):
    request.session["org_id"] = org.id if org else None
    request.session["servicing"] = servicing


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
        "recover",
        "two_factor_enable",
        "two_factor_disable",
        "two_factor_tokens",
        "account",
        "tokens",
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
        class Form(forms.Form):
            email = forms.EmailField(
                required=True, widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Email address")})
            )

            def clean_email(self):
                email = self.cleaned_data["email"].lower().strip()

                self.user = User.objects.filter(email__iexact=email).first()

                # error if we've sent a recovery email to this user recently to prevent flooding
                five_mins_ago = timezone.now() - timedelta(minutes=5)
                if self.user and self.user.recovery_tokens.filter(created_on__gt=five_mins_ago).exists():
                    raise forms.ValidationError(_("A recovery email was already sent to this address recently."))

                return email

        form_class = Form
        permission = None
        title = _("Password Reset")
        success_message = _("An email has been sent to your account with further instructions.")
        success_url = "@users.user_login"
        fields = ("email",)

        def form_valid(self, form):
            email = form.cleaned_data["email"]
            user = form.user

            if user:
                # delete any previously generated recovery tokens and create a new one
                user.recovery_tokens.all().delete()
                token = user.recovery_tokens.create(token=generate_secret(32))

                sender = EmailSender.from_email_type(self.request.branding, "notifications")
                sender.send(
                    [user.email],
                    _("Password Recovery Request"),
                    "orgs/email/user_forget",
                    {"user": user, "path": reverse("orgs.user_recover", args=[token.token])},
                )
            else:
                # no user, check if we have an invite for the email and resend that
                invite = Invitation.objects.filter(is_active=True, email__iexact=email).first()
                if invite:
                    invite.send()

            return super().form_valid(form)

    class Recover(ComponentFormMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            new_password = forms.CharField(
                validators=[validate_password],
                widget=forms.PasswordInput(attrs={"widget_only": True, "placeholder": _("New password")}),
                required=True,
            )
            confirm_password = forms.CharField(
                widget=forms.PasswordInput(attrs={"widget_only": True, "placeholder": _("Confirm")}), required=True
            )

            def clean(self):
                cleaned_data = super().clean()

                if not self.errors:
                    if cleaned_data.get("new_password") != cleaned_data.get("confirm_password"):
                        raise forms.ValidationError(_("New password and confirmation don't match."))

                return self.cleaned_data

            class Meta:
                model = User
                fields = ("new_password", "confirm_password")

        form_class = Form
        permission = None
        title = _("Password Reset")
        success_url = "@users.user_login"
        success_message = _("Your password has been updated successfully.")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<token>\w+)/$" % (path, action)

        def pre_process(self, request, *args, **kwargs):
            # if token is too old, redirect to forget password
            if self.token.created_on < timezone.now() - timedelta(hours=1):
                messages.info(
                    request,
                    _("This link has expired. Please reinitiate the process by entering your email here."),
                )
                return HttpResponseRedirect(reverse("orgs.user_forget"))

            return super().pre_process(request, args, kwargs)

        @cached_property
        def token(self):
            return get_object_or_404(RecoveryToken, token=self.kwargs["token"])

        def get_object(self):
            return self.token.user

        def save(self, obj):
            obj.set_password(self.form.cleaned_data["new_password"])
            obj.save(update_fields=("password",))
            return obj

        def post_save(self, obj):
            obj = super().post_save(obj)

            # delete all recovery tokens for this user
            obj.recovery_tokens.all().delete()

            # delete any failed login records
            FailedLogin.objects.filter(username__iexact=obj.username).delete()

            return obj

    class Edit(ComponentFormMixin, InferUserMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            first_name = forms.CharField(
                label=_("First Name"), widget=InputWidget(attrs={"placeholder": _("Required")})
            )
            last_name = forms.CharField(label=_("Last Name"), widget=InputWidget(attrs={"placeholder": _("Required")}))
            email = forms.EmailField(required=True, label=_("Email"), widget=InputWidget())
            avatar = forms.ImageField(
                required=False, label=_("Profile Picture"), widget=ImagePickerWidget(attrs={"shape": "circle"})
            )
            current_password = forms.CharField(
                required=False,
                label=_("Current Password"),
                widget=InputWidget({"widget_only": True, "placeholder": _("Password Required"), "password": True}),
            )
            new_password = forms.CharField(
                required=False,
                label=_("New Password"),
                validators=[validate_password],
                widget=InputWidget(attrs={"placeholder": _("Optional"), "password": True}),
            )
            language = forms.ChoiceField(
                choices=settings.LANGUAGES, required=True, label=_("Website Language"), widget=SelectWidget()
            )

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

                existing = User.get_by_email(email)
                if existing and existing != user:
                    raise forms.ValidationError(_("Sorry, that email address is already taken."))

                return email

            class Meta:
                model = User
                fields = ("first_name", "last_name", "email", "avatar", "current_password", "new_password", "language")

        form_class = Form
        success_url = "@orgs.user_edit"

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def derive_exclude(self):
            return ["language"] if len(settings.LANGUAGES) == 1 else []

        def derive_initial(self):
            initial = super().derive_initial()
            initial["language"] = self.object.settings.language
            initial["avatar"] = self.object.settings.avatar
            return initial

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            # keep our username and email in sync and record if email is changing
            obj.username = obj.email

            # get existing email address to know if it's changing
            obj._prev_email = User.objects.get(id=obj.id).email

            # figure out if password is being changed and if so update it
            new_password = self.form.cleaned_data["new_password"]
            current_password = self.form.cleaned_data["current_password"]
            if new_password and new_password != current_password:
                obj.set_password(self.form.cleaned_data["new_password"])
                obj._password_changed = True
            else:
                obj._password_changed = False

            return obj

        def post_save(self, obj):
            from temba.notifications.types.builtin import UserEmailNotificationType, UserPasswordNotificationType

            obj = super().post_save(obj)

            if obj.email != obj._prev_email:
                obj.settings.email_status = UserSettings.STATUS_UNVERIFIED
                obj.settings.email_verification_secret = generate_secret(64)  # make old verification links unusable

                RecoveryToken.objects.filter(user=obj).delete()  # make old password recovery links unusable

                UserEmailNotificationType.create(self.request.org, self.request.user, obj._prev_email)

            if obj._password_changed:
                update_session_auth_hash(self.request, self.request.user)

                UserPasswordNotificationType.create(self.request.org, self.request.user)

            language = self.form.cleaned_data.get("language")
            if language:
                obj.settings.language = language

            obj.settings.avatar = self.form.cleaned_data["avatar"]
            obj.settings.save(update_fields=("language", "email_status", "email_verification_secret", "avatar"))
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
            r = get_redis_connection()
            if request.user.settings.email_status == UserSettings.STATUS_VERIFIED:
                return HttpResponseRedirect(reverse("orgs.user_account"))
            elif r.exists(f"send_verification_email:{request.user.email}".lower()):
                messages.info(request, _("Verification email already sent. You can retry in 10 minutes."))
                return HttpResponseRedirect(reverse("orgs.user_account"))

            return super().pre_process(request, *args, **kwargs)

        def form_valid(self, form):
            send_user_verification_email.delay(self.request.org.id, self.object.id)

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
            context["num_api_tokens"] = self.request.user.get_api_tokens(self.request.org).count()
            return context

        def derive_formax_sections(self, formax, context):
            formax.add_section("profile", reverse("orgs.user_edit"), icon="user")

    class Tokens(SpaMixin, InferUserMixin, ContentMenuMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            new = forms.BooleanField(required=False)

            class Meta:
                model = User
                fields = ()

        form_class = Form
        title = _("API Tokens")
        menu_path = "/settings/account"
        success_url = "@orgs.user_tokens"
        token_limit = 3

        def build_content_menu(self, menu):
            if self.request.user.get_api_tokens(self.request.org).count() < self.token_limit:
                menu.add_url_post(_("New Token"), reverse("orgs.user_tokens") + "?new=1", as_button=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["tokens"] = self.request.user.get_api_tokens(self.request.org).order_by("created")
            context["token_limit"] = self.token_limit
            return context

        def form_valid(self, form):
            if self.request.user.get_api_tokens(self.request.org).count() < self.token_limit:
                APIToken.create(self.request.org, self.request.user)

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
        "flow_smtp",
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
                            perm="orgs.org_dashboard",
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

                menu.append(self.create_divider())
                if self.has_org_perm("orgs.org_export"):
                    menu.append(self.create_menu_item(name=_("Export"), icon="export", href="orgs.org_export"))

                if self.has_org_perm("orgs.orgimport_create"):
                    menu.append(self.create_menu_item(name=_("Import"), icon="import", href="orgs.orgimport_create"))

                if self.has_org_perm("channels.channel_read"):
                    from temba.channels.views import get_channel_read_url

                    items = []

                    if self.has_org_perm("channels.channel_claim"):
                        items.append(
                            self.create_menu_item(name=_("New Channel"), href="channels.channel_claim", icon="add")
                        )

                    channels = org.channels.filter(is_active=True).order_by(Lower("name"))
                    for channel in channels:
                        items.append(
                            self.create_menu_item(
                                menu_id=str(channel.uuid),
                                name=channel.name,
                                href=get_channel_read_url(channel),
                                icon=channel.type.icon,
                            )
                        )

                    if len(items):
                        menu.append(self.create_menu_item(name=_("Channels"), items=items, inline=True))

                if self.has_org_perm("classifiers.classifier_read"):
                    items = []

                    if self.has_org_perm("classifiers.classifier_connect"):
                        items.append(
                            self.create_menu_item(
                                name=_("New Classifier"), href="classifiers.classifier_connect", icon="add"
                            )
                        )

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
                    self.create_menu_item(
                        menu_id=other_org.id,
                        name=other_org.name,
                        avatar=other_org.name,
                        event="temba-workspace-choosen",
                    )
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
                            self.create_menu_item(
                                menu_id="settings", name=org.name, avatar=org.name, event="temba-workspace-settings"
                            ),
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
        menu_path = "/settings/export"
        submit_button_name = _("Export")
        success_message = _("We are preparing your export and you will get a notification when it is complete.")

        def post(self, request, *args, **kwargs):
            org = self.get_object()
            user = self.request.user

            flow_ids = [elt for elt in self.request.POST.getlist("flows") if elt]
            campaign_ids = [elt for elt in self.request.POST.getlist("campaigns") if elt]

            # fetch the selected flows and campaigns
            flows = Flow.objects.filter(id__in=flow_ids, org=org, is_active=True)
            campaigns = Campaign.objects.filter(id__in=campaign_ids, org=org, is_active=True)

            export = DefinitionExport.create(org=org, user=user, flows=flows, campaigns=campaigns)

            on_transaction_commit(lambda: export.start())

            messages.info(self.request, self.success_message)

            return HttpResponseRedirect(reverse("orgs.org_workspace"))

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

    class FlowSmtp(InferOrgMixin, OrgPermsMixin, SmartFormView):
        form_class = SMTPForm

        def post(self, request, *args, **kwargs):
            if "disconnect" in request.POST:
                org = self.request.org
                org.flow_smtp = None
                org.modified_by = request.user
                org.save(update_fields=("flow_smtp", "modified_by", "modified_on"))

                return HttpResponseRedirect(reverse("orgs.org_workspace"))

            return super().post(request, *args, **kwargs)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            kwargs["initial"] = self.request.org.flow_smtp
            return kwargs

        def form_valid(self, form):
            org = self.request.org

            org.flow_smtp = form.cleaned_data["smtp_url"]
            org.modified_by = self.request.user
            org.save(update_fields=("flow_smtp", "modified_by", "modified_on"))

            return super().form_valid(form)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()

            def extract_from(smtp_url: str) -> str:
                return parse_smtp_url(smtp_url)[4]

            from_email_default = settings.FLOW_FROM_EMAIL
            if org.is_child and org.parent.flow_smtp:
                from_email_default = extract_from(org.parent.flow_smtp)

            from_email_custom = extract_from(org.flow_smtp) if org.flow_smtp else None

            context["from_email_default"] = from_email_default
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

    class ManageAccounts(SpaMixin, InferOrgMixin, ContentMenuMixin, OrgPermsMixin, SmartUpdateView):
        class AccountsForm(forms.ModelForm):
            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.org = org
                self.user_rows = []
                self.invite_rows = []
                self.add_per_user_fields(org)
                self.add_per_invite_fields(org)

            def add_per_user_fields(self, org: Org):
                role_choices = [(r.code, r.display) for r in (OrgRole.ADMINISTRATOR, OrgRole.EDITOR, OrgRole.AGENT)]
                role_choices_inc_viewer = role_choices + [(OrgRole.VIEWER.code, OrgRole.VIEWER.display)]

                for user in org.users.order_by("email"):
                    role = org.get_user_role(user)

                    role_field = forms.ChoiceField(
                        choices=role_choices_inc_viewer if role == OrgRole.VIEWER else role_choices,
                        required=True,
                        initial=role.code,
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
                        choices=[(r.code, r.display) for r in (OrgRole.ADMINISTRATOR, OrgRole.EDITOR, OrgRole.AGENT)],
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
                fields = ()

        form_class = AccountsForm
        success_url = "@orgs.org_manage_accounts"
        title = _("Users")
        menu_path = "/settings/users"

        def pre_process(self, request, *args, **kwargs):
            if Org.FEATURE_USERS not in request.org.features:
                return HttpResponseRedirect(reverse("orgs.org_workspace"))

        def build_content_menu(self, menu):
            menu.add_modax(_("Invite"), "invite-create", reverse("orgs.invitation_create"), as_button=True)

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.get_object()
            return kwargs

        def post_save(self, obj):
            obj = super().post_save(obj)
            org = self.get_object()

            # delete any invitations which have been checked for removal
            for invite in self.form.get_submitted_invite_removals():
                org.invitations.filter(id=invite.id).delete()

            # update org users with new roles
            for user, new_role in self.form.get_submitted_roles().items():
                if not new_role:
                    org.remove_user(user)
                elif org.get_user_role(user) != new_role:
                    org.add_user(user, new_role)

            return obj

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.get_object()
            context["org"] = org
            context["has_invites"] = org.invitations.filter(is_active=True).exists()
            context["has_viewers"] = org.get_users(roles=[OrgRole.VIEWER]).exists()
            return context

        def get_success_url(self):
            still_in_org = self.get_object().has_user(self.request.user) or self.request.user.is_staff

            # if current user no longer belongs to this org, redirect to org chooser
            return reverse("orgs.org_manage_accounts") if still_in_org else reverse("orgs.org_choose")

    class ManageAccountsSubOrg(ManageAccounts):
        menu_path = "/settings/workspaces"

        def pre_process(self, request, *args, **kwargs):
            pass

        def build_content_menu(self, menu):
            menu.add_modax(
                _("Invite"),
                "invite-create",
                reverse("orgs.invitation_create") + f"?org={self.target_org.id}",
                as_button=True,
            )

        def derive_title(self):
            return self.target_org.name

        def get_object(self, *args, **kwargs):
            return self.target_org

        @cached_property
        def target_org(self):
            return get_object_or_404(self.request.org.children.filter(id=int(self.request.GET.get("org", 0))))

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
            switch_to_org(self.request, form.cleaned_data["other_org"], servicing=True)
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
            user = User.get_by_email(self.invitation.email)
            if user and self.invitation.email.lower() == request.user.username.lower():
                return HttpResponseRedirect(reverse("orgs.org_join_accept", args=[secret]))

            logout(request)

            if not user:
                return HttpResponseRedirect(reverse("orgs.org_join_signup", args=[secret]))

    class JoinSignup(NoNavMixin, InvitationMixin, SmartUpdateView):
        """
        Sign up form for new users to accept a workspace invitations.
        """

        form_class = SignupForm
        fields = ("first_name", "last_name", "password")
        success_url = "@orgs.org_start"
        submit_button_name = _("Sign Up")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            resp = super().pre_process(request, *args, **kwargs)
            if resp:
                return resp

            # if user already exists, we shouldn't be here
            if User.get_by_email(self.invitation.email):
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
            user = User.get_by_email(self.invitation.email)
            if not user or self.invitation.email != request.user.username:
                return HttpResponseRedirect(reverse("orgs.org_join", args=[self.kwargs["secret"]]))

            return None

        def save(self, obj):
            obj.add_user(self.request.user, self.invitation.role)

            self.invitation.release()

            switch_to_org(self.request, obj)

    class Grant(SpaMixin, ComponentFormMixin, NonAtomicMixin, SmartCreateView):
        class Form(forms.ModelForm):
            first_name = forms.CharField(
                help_text=_("The first name of the workspace administrator"),
                max_length=User._meta.get_field("first_name").max_length,
            )
            last_name = forms.CharField(
                help_text=_("Your last name of the workspace administrator"),
                max_length=User._meta.get_field("last_name").max_length,
            )
            email = forms.EmailField(
                help_text=_("Their email address"), max_length=User._meta.get_field("username").max_length
            )
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
                    if User.get_by_email(email):
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

        title = _("Create Workspace Account")
        form_class = Form
        success_message = "Workspace successfully created."
        submit_button_name = _("Create")
        success_url = "@orgs.org_grant"
        menu_path = "/settings"

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
        form_class = SignupForm
        permission = None

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
        class Form(forms.ModelForm):
            class Meta:
                model = Org
                fields = ("id",)

        form_class = Form
        success_url = "@orgs.org_workspace"

        def save(self, obj):
            org = self.request.org

            # if org has an existing Prometheus token, disable it, otherwise create one
            if org.prometheus_token:
                org.prometheus_token = None
                org.save(update_fields=("prometheus_token",))
            else:
                org.prometheus_token = generate_secret(40)
                org.save(update_fields=("prometheus_token",))

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            org = self.request.org
            context["prometheus_url"] = f"https://{org.branding['domain']}/mr/org/{org.uuid}/metrics"
            return context

    class Workspace(SpaMixin, FormaxMixin, ContentMenuMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Workspace")
        menu_path = "/settings/workspace"

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("orgs.org_edit"):
                formax.add_section("org", reverse("orgs.org_edit"), icon="settings")

            if self.has_org_perm("orgs.org_languages"):
                formax.add_section("languages", reverse("orgs.org_languages"), icon="language")

            if self.has_org_perm("orgs.org_country") and "locations" in settings.FEATURES:
                formax.add_section("country", reverse("orgs.org_country"), icon="location")

            if self.has_org_perm("orgs.org_flow_smtp"):
                formax.add_section("email", reverse("orgs.org_flow_smtp"), icon="email")

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

        form_class = Form

        def derive_exclude(self):
            return ["language"] if len(settings.LANGUAGES) == 1 else []

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

        form_class = CountryForm

    class Languages(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class LanguageForm(forms.ModelForm):
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

        success_url = "@orgs.org_languages"
        form_class = LanguageForm

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
            if self.request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest" and not self.request.META.get(
                "HTTP_X_FORMAX", False
            ):
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


class InvitationCRUDL(SmartCRUDL):
    model = Invitation
    actions = ("create",)

    class Create(SpaMixin, ModalMixin, OrgPermsMixin, SmartCreateView):
        class Form(forms.ModelForm):
            ROLE_CHOICES = [(r.code, r.display) for r in (OrgRole.AGENT, OrgRole.EDITOR, OrgRole.ADMINISTRATOR)]

            email = forms.EmailField(widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Email Address")}))
            role = forms.ChoiceField(
                choices=ROLE_CHOICES, initial=OrgRole.EDITOR.code, label=_("Role"), widget=SelectWidget()
            )

            def __init__(self, org, *args, **kwargs):
                self.org = org

                super().__init__(*args, **kwargs)

            def clean_email(self):
                email = self.cleaned_data["email"]

                if self.org.users.filter(email__iexact=email).exists():
                    raise ValidationError(_("User is already a member of this workspace."))

                if self.org.invitations.filter(email__iexact=email, is_active=True).exists():
                    raise ValidationError(_("User has already been invited to this workspace."))

                return email

            class Meta:
                model = Invitation
                fields = ("email", "role")

        form_class = Form
        title = ""
        submit_button_name = _("Send")
        success_url = "@orgs.org_manage_accounts"

        def get_dest_org(self):
            org_id = self.request.GET.get("org")
            if org_id:
                return get_object_or_404(self.request.org.children.filter(id=org_id))

            return self.request.org

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.get_dest_org()
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["validity_days"] = settings.INVITATION_VALIDITY.days
            return context

        def pre_save(self, obj):
            org = self.get_dest_org()

            assert Org.FEATURE_USERS in org.features

            obj.org = org
            obj.user_group = self.form.cleaned_data["role"]

            return super().pre_save(obj)

        def post_save(self, obj):
            obj.send()

            return super().post_save(obj)


class OrgImportCRUDL(SmartCRUDL):
    model = OrgImport
    actions = ("create", "read")

    class Create(SpaMixin, OrgPermsMixin, SmartCreateView):
        menu_path = "/settings/import"

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
            obj.start()
            return obj

    class Read(SpaMixin, OrgPermsMixin, SmartReadView):
        menu_path = "/settings/import"

        def derive_title(self):
            return _("Import Flows and Campaigns")


class ExportCRUDL(SmartCRUDL):
    model = Export
    actions = ("download",)

    class Download(SpaMixin, ContentMenuMixin, NotificationTargetMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        menu_path = "/settings/workspace"
        title = _("Export")

        def get(self, request, *args, **kwargs):
            if str_to_bool(request.GET.get("raw", 0)):
                export = self.get_object()

                return HttpResponseRedirect(export.get_raw_url())

            return super().get(request, *args, **kwargs)

        def build_content_menu(self, menu):
            menu.add_js("export_download", _("Download"), as_button=True)

        def get_template_names(self):
            return [self.object.type.download_template]

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["extension"] = self.object.path.rsplit(".", 1)[1]
            context.update(**self.object.type.get_download_context(self.object))
            return context

        def get_notification_scope(self) -> tuple[str, str]:
            return "export:finished", self.get_object().get_notification_scope()


class BaseExportView(ModalMixin, OrgPermsMixin, SmartFormView):
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
