from collections import OrderedDict
from datetime import timedelta
from urllib.parse import quote

import iso8601
import pyotp
from django_redis import get_redis_connection
from packaging.version import Version
from smartmin.users.models import FailedLogin, RecoveryToken
from smartmin.views import (
    SmartCreateView,
    SmartCRUDL,
    SmartDeleteView,
    SmartFormView,
    SmartReadView,
    SmartTemplateView,
    SmartUpdateView,
)

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.password_validation import validate_password
from django.contrib.auth.views import LoginView as AuthLoginView
from django.core.exceptions import ValidationError
from django.db.models import Q
from django.db.models.functions import Lower
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import DjangoUnicodeDecodeError, force_str
from django.utils.functional import cached_property
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import View

from temba.api.models import Resthook
from temba.campaigns.models import Campaign
from temba.flows.models import Flow
from temba.formax import FormaxMixin, FormaxSectionMixin
from temba.notifications.mixins import NotificationTargetMixin
from temba.orgs.tasks import send_user_verification_email
from temba.tickets.models import Team
from temba.utils import analytics, json, languages, on_transaction_commit, str_to_bool
from temba.utils.email import EmailSender, parse_smtp_url
from temba.utils.fields import (
    ArbitraryJsonChoiceField,
    CheckboxWidget,
    ImagePickerWidget,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
)
from temba.utils.text import generate_secret
from temba.utils.timezones import TimeZoneFormField
from temba.utils.views.mixins import (
    ComponentFormMixin,
    ContextMenuMixin,
    ModalFormMixin,
    NonAtomicMixin,
    NoNavMixin,
    PostOnlyMixin,
    RequireRecentAuthMixin,
    SpaMixin,
)

from ..models import (
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
from .base import BaseDeleteModal, BaseListView, BaseMenuView
from .forms import SignupForm, SMTPForm
from .mixins import InferOrgMixin, InferUserMixin, OrgObjPermsMixin, OrgPermsMixin, RequireFeatureMixin

# session key for storing a two-factor enabled user's id once we've checked their password
TWO_FACTOR_USER_SESSION_KEY = "_two_factor_user_id"
TWO_FACTOR_STARTED_SESSION_KEY = "_two_factor_started_on"
TWO_FACTOR_LIMIT_SECONDS = 5 * 60


def switch_to_org(request, org, *, servicing: bool = False):
    request.session["org_id"] = org.id if org else None
    request.session["servicing"] = servicing


def check_login(request):
    """
    Simple view that checks whether we actually need to log in. This is needed on the live site
    because we serve the main page as http:// but the logged in pages as https:// and only store
    the cookies on the SSL connection. This view will be called in https:// land where we will
    check whether we are logged in, if so then we will redirect to the org chooser, otherwise we take
    them to the user login page.
    """

    if request.user.is_authenticated:
        return HttpResponseRedirect(reverse("orgs.org_choose"))
    else:
        return HttpResponseRedirect(reverse("orgs.login"))


class IntegrationFormaxView(FormaxSectionMixin, ComponentFormMixin, OrgPermsMixin, SmartFormView):
    class Form(forms.Form):
        def __init__(self, request, integration_type, **kwargs):
            self.request = request
            self.channel_type = integration_type
            super().__init__(**kwargs)

    permission = "orgs.org_manage_integrations"
    integration_type = None
    success_url = "@orgs.org_workspace"

    def __init__(self, integration_type):
        self.integration_type = integration_type

        super().__init__()

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        kwargs["integration_type"] = self.integration_type
        return kwargs

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["integration_type"] = self.integration_type
        context["integration_connected"] = self.integration_type.is_connected(self.request.org)
        return context

    def form_valid(self, form):
        response = self.render_to_response(self.get_context_data(form=form))
        response["REDIRECT"] = self.get_success_url()
        return response


class LoginView(AuthLoginView):
    """
    Overrides the auth login view to add support for tracking failed logins and 2FA.
    """

    template_name = "orgs/login/login.html"
    two_factor = True

    def post(self, request, *args, **kwargs):
        form = self.get_form()

        form_is_valid = form.is_valid()  # clean form data

        lockout_timeout = getattr(settings, "USER_LOCKOUT_TIMEOUT", 10)
        failed_login_limit = getattr(settings, "USER_FAILED_LOGIN_LIMIT", 5)

        username = self.get_username(form)
        if not username:
            return self.form_invalid(form)

        user = User.objects.filter(username__iexact=username).first()
        valid_password = False

        # this could be a valid login by a user
        if user:
            # incorrect password?  create a failed login token
            valid_password = user.check_password(form.cleaned_data.get("password"))

        if not user or not valid_password:
            FailedLogin.objects.create(username=username)

        failures = FailedLogin.objects.filter(username__iexact=username)

        # if the failures reset after a period of time, then limit our query to that interval
        if lockout_timeout > 0:
            bad_interval = timezone.now() - timedelta(minutes=lockout_timeout)
            failures = failures.filter(failed_on__gt=bad_interval)

        # if there are too many failed logins, take them to the failed page
        if len(failures) >= failed_login_limit:
            logout(request)

            return HttpResponseRedirect(reverse("orgs.user_failed"))

        # pass through the normal login process
        if form_is_valid:
            return self.form_valid(form)
        else:
            return self.form_invalid(form)

    def form_valid(self, form):
        user = form.get_user()

        if self.two_factor and user.settings.two_factor_enabled:
            self.request.session[TWO_FACTOR_USER_SESSION_KEY] = str(user.id)
            self.request.session[TWO_FACTOR_STARTED_SESSION_KEY] = timezone.now().isoformat()

            verify_url = reverse("orgs.two_factor_verify")
            redirect_url = self.get_redirect_url()
            if redirect_url:
                verify_url += f"?{self.redirect_field_name}={quote(redirect_url)}"

            return HttpResponseRedirect(verify_url)

        user.record_auth()

        # clean up any failed logins for this username
        FailedLogin.objects.filter(username__iexact=self.get_username(form)).delete()

        return super().form_valid(form)

    def get_username(self, form):
        return form.cleaned_data.get("username")


class LogoutView(View):
    """
    Logouts user on a POST and redirects to the login page.
    """

    @csrf_exempt
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        logout(request)

        return HttpResponseRedirect(reverse("orgs.login"))


class BaseTwoFactorView(AuthLoginView):
    def dispatch(self, request, *args, **kwargs):
        # redirect back to login view if user hasn't completed that yet
        user = self.get_user()
        if not user:
            return HttpResponseRedirect(reverse("orgs.login"))

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

            return HttpResponseRedirect(reverse("orgs.user_failed"))

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


class ConfirmAccessView(LoginView):
    """
    Overrides the login view to provide a view for an already logged in user to re-authenticate.
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
    two_factor = False

    def dispatch(self, request, *args, **kwargs):
        if not self.request.user.is_authenticated:
            return HttpResponseRedirect(reverse("orgs.login"))

        return super().dispatch(request, *args, **kwargs)

    def get_username(self, form):
        return self.request.user.username


class UserCRUDL(SmartCRUDL):
    model = User
    actions = (
        "list",
        "team",
        "update",
        "delete",
        "edit",
        "forget",
        "recover",
        "failed",
        "two_factor_enable",
        "two_factor_disable",
        "two_factor_tokens",
        "account",
        "verify_email",
        "send_verification_email",
    )

    class List(RequireFeatureMixin, SpaMixin, BaseListView):
        require_feature = Org.FEATURE_USERS
        title = _("Users")
        menu_path = "/settings/users"
        search_fields = ("email__icontains", "first_name__icontains", "last_name__icontains")

        def derive_queryset(self, **kwargs):
            qs = (
                super(BaseListView, self)
                .derive_queryset(**kwargs)
                .filter(id__in=self.request.org.get_users().values_list("id", flat=True))
                .order_by(Lower("email"))
                .select_related("settings")
            )

            if not self.request.user.is_staff:
                qs = qs.exclude(settings__is_system=True)

            return qs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            # annotate the users with their roles and teams
            for user in context["object_list"]:
                membership = self.request.org.get_membership(user)
                user.role = membership.role
                user.team = membership.team

            context["has_viewers"] = self.request.org.get_users(roles=[OrgRole.VIEWER]).exists()
            context["has_teams"] = Org.FEATURE_TEAMS in self.request.org.features

            admins = self.request.org.get_users(roles=[OrgRole.ADMINISTRATOR])
            if not self.request.user.is_staff:
                admins = admins.exclude(settings__is_system=True)
            context["admin_count"] = admins.count()

            return context

    class Team(RequireFeatureMixin, SpaMixin, ContextMenuMixin, BaseListView):
        permission = "orgs.user_list"
        require_feature = Org.FEATURE_TEAMS
        menu_path = "/settings/teams"
        search_fields = ("email__icontains", "first_name__icontains", "last_name__icontains")

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<team_id>\d+)/$" % (path, action)

        def derive_title(self):
            return self.team.name

        @cached_property
        def team(self):
            from temba.tickets.models import Team

            return get_object_or_404(Team, id=self.kwargs["team_id"])

        def build_context_menu(self, menu):
            if not self.team.is_system:
                if self.has_org_perm("tickets.team_update"):
                    menu.add_modax(
                        _("Edit"),
                        "update-team",
                        reverse("tickets.team_update", args=[self.team.id]),
                        title=_("Edit Team"),
                        as_button=True,
                    )
                if self.has_org_perm("tickets.team_delete"):
                    menu.add_modax(
                        _("Delete"),
                        "delete-team",
                        reverse("tickets.team_delete", args=[self.team.id]),
                        title=_("Delete Team"),
                    )

        def derive_queryset(self, **kwargs):
            return (
                super(BaseListView, self)
                .derive_queryset(**kwargs)
                .filter(id__in=self.team.get_users().values_list("id", flat=True))
                .order_by(Lower("email"))
            )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["team"] = self.team
            context["team_topics"] = self.team.topics.order_by(Lower("name"))
            return context

    class Update(RequireFeatureMixin, ModalFormMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            role = forms.ChoiceField(choices=OrgRole.choices(), required=True, label=_("Role"), widget=SelectWidget())
            team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False, widget=SelectWidget())

            def __init__(self, org, *args, **kwargs):
                self.org = org

                super().__init__(*args, **kwargs)

                self.fields["team"].queryset = org.teams.filter(is_active=True).order_by(Lower("name"))

            class Meta:
                model = User
                fields = ("role", "team")

        form_class = Form
        require_feature = Org.FEATURE_USERS

        def get_object_org(self):
            return self.request.org

        def get_queryset(self):
            return self.request.org.get_users().exclude(settings__is_system=True)

        def derive_exclude(self):
            return [] if Org.FEATURE_TEAMS in self.request.org.features else ["team"]

        def derive_initial(self):
            membership = self.request.org.get_membership(self.object)
            return {
                # viewers default to editors
                "role": OrgRole.EDITOR.code if membership.role == OrgRole.VIEWER else membership.role.code,
                "team": membership.team,
            }

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def save(self, obj):
            role = OrgRole.from_code(self.form.cleaned_data["role"])
            team = self.form.cleaned_data.get("team")
            team = (team or self.request.org.default_ticket_team) if role == OrgRole.AGENT else None

            # don't update if user is the last administrator and role is being changed to something else
            has_other_admins = self.request.org.get_admins().exclude(id=obj.id).exists()
            if role != OrgRole.ADMINISTRATOR and not has_other_admins:
                return obj

            self.request.org.add_user(obj, role, team=team)
            return obj

        def get_success_url(self):
            return reverse("orgs.user_list") if self.has_org_perm("orgs.user_list") else reverse("orgs.org_start")

    class Delete(RequireFeatureMixin, OrgObjPermsMixin, SmartDeleteView):
        permission = "orgs.user_update"
        require_feature = Org.FEATURE_USERS
        fields = ("id",)
        submit_button_name = _("Remove")
        cancel_url = "@orgs.user_list"
        redirect_url = "@orgs.user_list"

        def get_object_org(self):
            return self.request.org

        def get_queryset(self):
            return self.request.org.get_users().exclude(settings__is_system=True)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["submit_button_name"] = self.submit_button_name
            return context

        def post(self, request, *args, **kwargs):
            user = self.get_object()

            # only actually remove user if they're not the last administator
            if self.request.org.get_admins().exclude(id=user.id).exists():
                self.request.org.remove_user(user)

            return HttpResponseRedirect(self.get_redirect_url())

        def get_redirect_url(self):
            still_in_org = self.request.org.has_user(self.request.user) or self.request.user.is_staff

            # if current user no longer belongs to this org, redirect to org chooser
            return reverse("orgs.user_list") if still_in_org else reverse("orgs.org_choose")

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
        success_url = "@orgs.login"
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
        success_url = "@orgs.login"
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

    class Failed(SmartTemplateView):
        permission = None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["lockout_timeout"] = getattr(settings, "USER_LOCKOUT_TIMEOUT", 10)
            return context

    class Edit(FormaxSectionMixin, ComponentFormMixin, InferUserMixin, SmartUpdateView):
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
    model = Org
    actions = (
        "signup",
        "start",
        "edit",
        "update",
        "join",
        "join_signup",
        "join_accept",
        "grant",
        "choose",
        "delete",
        "menu",
        "country",
        "languages",
        "list",
        "create",
        "export",
        "prometheus",
        "resthooks",
        "flow_smtp",
        "workspace",
    )

    class Menu(BaseMenuView):
        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/((?P<submenu>[A-z]+)/)?$" % (path, action)

        def has_permission(self, request, *args, **kwargs):
            # allow staff access without an org since this view includes staff menu
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

                if self.request.user.is_authenticated:
                    menu.append(
                        self.create_menu_item(
                            menu_id="account",
                            name=_("Account"),
                            icon="account",
                            href=reverse("orgs.user_account"),
                        )
                    )

                menu.append(self.create_menu_item(name=_("Resthooks"), icon="resthooks", href="orgs.org_resthooks"))

                if self.has_org_perm("notifications.incident_list"):
                    menu.append(
                        self.create_menu_item(name=_("Incidents"), icon="incidents", href="notifications.incident_list")
                    )

                if Org.FEATURE_CHILD_ORGS in org.features and self.has_org_perm("orgs.org_list"):
                    menu.append(self.create_divider())
                    menu.append(
                        self.create_menu_item(
                            name=_("Workspaces"),
                            icon="children",
                            href="orgs.org_list",
                            count=org.children.filter(is_active=True).count() + 1,
                        )
                    )
                    menu.append(
                        self.create_menu_item(
                            menu_id="dashboard",
                            name=_("Dashboard"),
                            icon="dashboard",
                            href="dashboard.dashboard_home",
                            perm="orgs.org_dashboard",
                        )
                    )

                if Org.FEATURE_USERS in org.features and self.has_org_perm("orgs.user_list"):
                    menu.append(self.create_divider())
                    menu.append(
                        self.create_menu_item(
                            name=_("Users"), icon="users", href="orgs.user_list", count=org.users.count()
                        )
                    )
                    menu.append(
                        self.create_menu_item(
                            name=_("Invitations"),
                            icon="invitations",
                            href="orgs.invitation_list",
                            count=org.invitations.filter(is_active=True).count(),
                        )
                    )
                    if Org.FEATURE_TEAMS in org.features:
                        menu.append(
                            self.create_menu_item(
                                name=_("Teams"),
                                icon="agent",
                                href="tickets.team_list",
                                count=org.teams.filter(is_active=True).count(),
                            )
                        )

                if self.has_org_perm("orgs.org_export"):
                    menu.append(self.create_divider())
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
                        href=reverse("staff.org_list"),
                    ),
                    self.create_menu_item(
                        menu_id="users",
                        name=_("Users"),
                        icon="users",
                        href=reverse("staff.user_list"),
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
                                href=reverse("orgs.logout"),
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

                if not self.has_org_perm("orgs.org_workspace"):
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
        readonly_servicing = False

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

    class FlowSmtp(FormaxSectionMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
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

    class Update(ModalFormMixin, OrgObjPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            name = forms.CharField(max_length=128, label=_("Name"), widget=InputWidget())
            timezone = TimeZoneFormField(label=_("Timezone"), widget=SelectWidget(attrs={"searchable": True}))

            class Meta:
                model = Org
                fields = ("name", "timezone", "date_format", "language")
                widgets = {"date_format": SelectWidget(), "language": SelectWidget()}

        form_class = Form
        success_url = "@orgs.org_list"

        def get_object_org(self):
            return self.request.org

        def get_queryset(self, *args, **kwargs):
            return self.request.org.children.all()

    class Delete(ModalFormMixin, OrgObjPermsMixin, SmartDeleteView):
        cancel_url = "@orgs.org_list"
        success_url = "@orgs.org_list"
        fields = ("id",)
        submit_button_name = _("Delete")

        def get_object_org(self):
            return self.request.org

        def get_queryset(self, *args, **kwargs):
            return self.request.org.children.all()

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["delete_on"] = timezone.now() + timedelta(days=Org.DELETE_DELAY_DAYS)
            return context

        def post(self, request, *args, **kwargs):
            assert self.get_object().is_child, "can only delete child orgs"

            self.object = self.get_object()
            self.object.release(request.user)
            return self.render_modal_response()

    class List(RequireFeatureMixin, SpaMixin, ContextMenuMixin, BaseListView):
        require_feature = Org.FEATURE_CHILD_ORGS
        title = _("Workspaces")
        menu_path = "/settings/workspaces"
        search_fields = ("name__icontains",)

        def build_context_menu(self, menu):
            if self.has_org_perm("orgs.org_create"):
                menu.add_modax(
                    _("New"), "new_workspace", reverse("orgs.org_create"), title=_("New Workspace"), as_button=True
                )

        def derive_queryset(self, **kwargs):
            qs = super(BaseListView, self).derive_queryset(**kwargs)

            # return this org and its children
            org = self.request.org
            return (
                qs.filter(Q(id=org.id) | Q(id__in=[c.id for c in org.children.all()]))
                .filter(is_active=True)
                .order_by("-parent", "name")
            )

    class Create(NonAtomicMixin, RequireFeatureMixin, ModalFormMixin, InferOrgMixin, OrgPermsMixin, SmartCreateView):
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
        require_feature = (Org.FEATURE_NEW_ORGS, Org.FEATURE_CHILD_ORGS)

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
                return reverse("orgs.org_list")

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

                response["X-Temba-Success"] = success_url
                return response

    class Start(SmartTemplateView):
        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def pre_process(self, request, *args, **kwargs):
            user = self.request.user
            org = self.request.org

            if not org:
                no_org_page = reverse("orgs.org_choose")
                if user.is_staff:
                    no_org_page = f"{reverse('staff.org_list')}?filter=active"
                return HttpResponseRedirect(no_org_page)

            role = org.get_user_role(user)

            return HttpResponseRedirect(reverse(role.start_view if role else "msgs.msg_inbox"))

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
                        return HttpResponseRedirect(reverse("staff.org_list"))

                    # for regular users, if there's no orgs, log them out with a message
                    messages.info(request, _("No workspaces for this account, please contact your administrator."))
                    logout(request)
                    return HttpResponseRedirect(reverse("orgs.login"))
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

            self.invitation.accept(user)

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
            self.invitation.accept(self.request.user)

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

    class Prometheus(FormaxSectionMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
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

    class Workspace(SpaMixin, FormaxMixin, ContextMenuMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
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

    class Edit(FormaxSectionMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            name = forms.CharField(max_length=128, label=_("Name"), widget=InputWidget())
            timezone = TimeZoneFormField(label=_("Timezone"), widget=SelectWidget(attrs={"searchable": True}))

            class Meta:
                model = Org
                fields = ("name", "timezone", "date_format", "language")
                widgets = {"date_format": SelectWidget(), "language": SelectWidget()}

        form_class = Form

        def derive_exclude(self):
            return ["language"] if len(settings.LANGUAGES) == 1 else []

    class Country(FormaxSectionMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
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

    class Languages(FormaxSectionMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
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

        @property
        def permission(self):
            if self.request.META.get("HTTP_X_REQUESTED_WITH") == "XMLHttpRequest" and self.request.method == "GET":
                return "orgs.org_languages"
            else:
                return "orgs.org_country"


class InvitationCRUDL(SmartCRUDL):
    model = Invitation
    actions = ("list", "create", "delete")

    class List(RequireFeatureMixin, SpaMixin, ContextMenuMixin, BaseListView):
        require_feature = Org.FEATURE_USERS
        title = _("Invitations")
        menu_path = "/settings/invitations"
        default_order = ("-created_on",)

        def build_context_menu(self, menu):
            menu.add_modax(
                _("New"),
                "invitation-create",
                reverse("orgs.invitation_create"),
                title=_("New Invitation"),
                as_button=True,
            )

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["validity_days"] = settings.INVITATION_VALIDITY.days
            context["has_teams"] = Org.FEATURE_TEAMS in self.request.org.features
            return context

    class Create(RequireFeatureMixin, ModalFormMixin, OrgPermsMixin, SmartCreateView):
        readonly_servicing = False

        class Form(forms.ModelForm):
            email = forms.EmailField(widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Email Address")}))
            role = forms.ChoiceField(
                choices=OrgRole.choices(), initial=OrgRole.EDITOR.code, label=_("Role"), widget=SelectWidget()
            )
            team = forms.ModelChoiceField(queryset=Team.objects.none(), required=False, widget=SelectWidget())

            def __init__(self, org, *args, **kwargs):
                self.org = org

                super().__init__(*args, **kwargs)

                self.fields["team"].queryset = org.teams.filter(is_active=True).order_by(Lower("name"))

            def clean_email(self):
                email = self.cleaned_data["email"]

                if self.org.users.filter(email__iexact=email).exists():
                    raise ValidationError(_("User is already a member of this workspace."))

                if self.org.invitations.filter(email__iexact=email, is_active=True).exists():
                    raise ValidationError(_("User has already been invited to this workspace."))

                return email

            class Meta:
                model = Invitation
                fields = ("email", "role", "team")

        form_class = Form
        require_feature = Org.FEATURE_USERS
        title = ""
        submit_button_name = _("Send")
        success_url = "@orgs.invitation_list"

        def derive_exclude(self):
            return [] if Org.FEATURE_TEAMS in self.request.org.features else ["team"]

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["validity_days"] = settings.INVITATION_VALIDITY.days
            return context

        def save(self, obj):
            role = OrgRole.from_code(self.form.cleaned_data["role"])
            team = (obj.team or self.request.org.default_ticket_team) if role == OrgRole.AGENT else None

            self.object = Invitation.create(self.request.org, self.request.user, obj.email, role, team=team)

        def post_save(self, obj):
            obj.send()

            return super().post_save(obj)

    class Delete(RequireFeatureMixin, BaseDeleteModal):
        require_feature = Org.FEATURE_USERS
        cancel_url = "@orgs.invitation_list"
        redirect_url = "@orgs.invitation_list"


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

                for flow in json_data.get("flows", []):
                    spec = flow.get("spec_version")
                    if spec and Version(spec) > Version(Flow.CURRENT_SPEC_VERSION):
                        raise ValidationError(_("This file contains flows with a version that is too new."))

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

    class Download(SpaMixin, ContextMenuMixin, NotificationTargetMixin, OrgObjPermsMixin, SmartReadView):
        slug_url_kwarg = "uuid"
        menu_path = "/settings/workspace"
        title = _("Export")

        def get(self, request, *args, **kwargs):
            if str_to_bool(request.GET.get("raw", 0)):
                export = self.get_object()

                return HttpResponseRedirect(export.get_raw_url())

            return super().get(request, *args, **kwargs)

        def build_context_menu(self, menu):
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
