import itertools
import logging
import random
import smtplib
import string
from collections import OrderedDict
from datetime import timedelta
from email.utils import parseaddr
from urllib.parse import parse_qs, quote, unquote, urlparse

import iso8601
import pyotp
import requests
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
from twilio.rest import Client

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
from django.forms import Form
from django.http import Http404, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import resolve_url
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.utils.encoding import DjangoUnicodeDecodeError, force_str
from django.utils.html import escape
from django.utils.safestring import mark_safe
from django.utils.text import slugify
from django.utils.translation import gettext_lazy as _
from django.views.decorators.csrf import csrf_exempt

from temba.api.models import APIToken, Resthook
from temba.campaigns.models import Campaign
from temba.channels.models import Channel
from temba.classifiers.models import Classifier
from temba.flows.models import Flow
from temba.formax import FormaxMixin
from temba.utils import analytics, get_anonymous_user, json, languages
from temba.utils.email import is_valid_address, send_template_email
from temba.utils.fields import (
    ArbitraryJsonChoiceField,
    CheckboxWidget,
    InputWidget,
    SelectMultipleWidget,
    SelectWidget,
    TembaChoiceField,
)
from temba.utils.http import http_headers
from temba.utils.timezones import TimeZoneFormField
from temba.utils.views import (
    ComponentFormMixin,
    ContentMenuMixin,
    NonAtomicMixin,
    RequireRecentAuthMixin,
    SpaMixin,
    StaffOnlyMixin,
)

from .models import BackupToken, IntegrationType, Invitation, Org, OrgRole, User

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
        if self.org:
            return self.get_user().has_org_perm(self.org, permission)
        return False

    def has_permission(self, request, *args, **kwargs):
        """
        Figures out if the current user has permissions for this view.
        """
        self.kwargs = kwargs
        self.args = args
        self.request = request
        self.org = self.derive_org()

        if self.get_user().is_staff and self.org:
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
        has_perm = super().has_permission(request, *args, **kwargs)

        if has_perm:
            user = self.request.user

            # user has global permission
            if user.has_perm(self.permission):
                return True

            return self.request.org == self.get_object_org()

        return False


class ModalMixin(SmartFormView):
    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        if "HTTP_X_PJAX" in self.request.META and "HTTP_X_FORMAX" not in self.request.META:  # pragma: no cover
            context["base_template"] = "smartmin/modal.html"
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

    success_url = "@orgs.org_home"

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
    template_name = "orgs/dependency_usages_modal.haml"

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
    template_name = "orgs/dependency_delete_modal.haml"

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

    template_name = "orgs/login/login.haml"

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
    template_name = "orgs/login/two_factor_verify.haml"


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
    template_name = "orgs/login/two_factor_backup.haml"


class ConfirmAccessView(SpaMixin, Login):
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

    template_name = "orgs/login/confirm_access.haml"
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
    @classmethod
    def derive_url_pattern(cls, path, action):
        return r"^%s/%s/$" % (path, action)

    def get_object(self, *args, **kwargs):
        return self.request.org


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
    )

    class Read(StaffOnlyMixin, ContentMenuMixin, SpaMixin, SmartReadView):
        fields = ("email", "date_joined")

        def build_content_menu(self, menu):
            menu.add_modax(
                _("Edit"),
                "user-update",
                reverse("orgs.user_update", args=[self.object.id]),
                title=_("Edit User"),
                as_button=True,
            )

            menu.add_modax(
                _("Delete"), "user-delete", reverse("orgs.user_delete", args=[self.object.id]), title=_("Delete User")
            )

    class List(StaffOnlyMixin, SpaMixin, SmartListView):
        fields = ("email", "name", "date_joined")
        ordering = ("-date_joined",)
        search_fields = ("email__icontains", "first_name__icontains", "last_name__icontains")
        filters = (("all", _("All")), ("beta", _("Beta")), ("staff", _("Staff")))

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
            brand = self.request.branding.get("brand")

            context = super().get_context_data(**kwargs)
            context["owned_orgs"] = self.get_object().get_owned_orgs(brand=brand)
            return context

        def post(self, request, *args, **kwargs):
            user = self.get_object()

            brand = self.request.branding.get("brand")
            user.release(self.request.user, brand=brand)

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
                subject = _("Password Recovery Request")
                template = "orgs/email/user_forget"

                token = "".join(random.choice(string.ascii_uppercase + string.digits) for x in range(32))
                RecoveryToken.objects.create(token=token, user=user)
                FailedLogin.objects.filter(username__iexact=user.username).delete()

                context = dict(user=user, path=f'{reverse("users.user_recover", args=[token])}')
                send_template_email(email, subject, template, context, self.request.branding)

            else:
                # No user, check if we have an invite for the email and resend that
                existing_invite = Invitation.objects.filter(is_active=True, email__iexact=email).first()
                if existing_invite:
                    existing_invite.send()

            return super().form_valid(form)

    class Edit(SmartUpdateView):
        class EditForm(forms.ModelForm):
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

        form_class = EditForm
        permission = "orgs.org_profile"
        success_message = ""

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/$" % (path, action)

        def get_object(self, *args, **kwargs):
            return self.request.user

        def derive_initial(self):
            initial = super().derive_initial()
            initial["language"] = self.get_object().settings.language
            return initial

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            # keep our username and email in sync
            obj.username = obj.email

            if self.form.cleaned_data["new_password"]:
                obj.set_password(self.form.cleaned_data["new_password"])

            return obj

        def post_save(self, obj):
            # save the user settings as well
            obj = super().post_save(obj)
            obj.settings.language = self.form.cleaned_data["language"]
            obj.settings.save()
            return obj

        def has_permission(self, request, *args, **kwargs):
            user = self.request.user

            if user.is_anonymous:
                return False

            if request.org:
                if not user.is_authenticated:  # pragma: needs cover
                    return False

                if request.org.has_user(user):
                    return True

            return False  # pragma: needs cover

    class TwoFactorEnable(SpaMixin, ComponentFormMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class Form(forms.Form):
            otp = forms.CharField(
                label="The generated OTP",
                widget=InputWidget(attrs={"placeholder": _("6-digit code")}),
                max_length=6,
                required=True,
            )
            password = forms.CharField(
                label="Your current login password",
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

            def clean_password(self):
                data = self.cleaned_data["password"]
                if not self.user.check_password(data):
                    raise forms.ValidationError(_("Password incorrect."))
                return data

        form_class = Form
        success_url = "@orgs.user_two_factor_tokens"
        success_message = _("Two-factor authentication enabled")
        submit_button_name = _("Enable")
        permission = "orgs.org_two_factor"
        title = _("Enable Two-factor Authentication")

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

    class TwoFactorDisable(SpaMixin, ComponentFormMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class Form(forms.Form):
            password = forms.CharField(
                label=" ",
                widget=InputWidget(attrs={"placeholder": _("Current password"), "password": True}),
                required=True,
            )

            def __init__(self, user, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.user = user

            def clean_password(self):
                data = self.cleaned_data["password"]
                if not self.user.check_password(data):
                    raise forms.ValidationError(_("Password incorrect."))
                return data

        form_class = Form
        success_url = "@orgs.org_home"
        success_message = _("Two-factor authentication disabled")
        submit_button_name = _("Disable")
        permission = "orgs.org_two_factor"
        title = _("Disable Two-factor Authentication")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["user"] = self.request.user
            return kwargs

        def form_valid(self, form):
            self.request.user.disable_2fa()
            self.request.user.record_auth()

            return super().form_valid(form)

    class TwoFactorTokens(
        SpaMixin, RequireRecentAuthMixin, InferOrgMixin, ContentMenuMixin, OrgPermsMixin, SmartTemplateView
    ):
        permission = "orgs.org_two_factor"
        title = _("Two-factor Authentication")

        def pre_process(self, request, *args, **kwargs):
            # if 2FA isn't enabled for this user, take them to the enable view instead
            if not self.request.user.settings.two_factor_enabled:
                return HttpResponseRedirect(reverse("orgs.user_two_factor_enable"))

            return super().pre_process(request, *args, **kwargs)

        def post(self, request, *args, **kwargs):
            BackupToken.generate_for_user(self.request.user)
            messages.info(request, _("Two-factor authentication backup tokens changed."))

            return super().get(request, *args, **kwargs)

        def build_content_menu(self, menu):
            if not self.is_spa():
                menu.add_link(_("Home"), reverse("orgs.org_home"))

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["backup_tokens"] = self.request.user.backup_tokens.order_by("id")
            return context

    class Account(SpaMixin, FormaxMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Account")
        permission = "orgs.org_account"

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["two_factor_enabled"] = self.request.user.settings.two_factor_enabled
            return context

        def derive_formax_sections(self, formax, context):
            if self.has_org_perm("orgs.org_profile"):
                formax.add_section("org", reverse("orgs.user_edit"), icon="icon-user")


class SpaView(InferOrgMixin, OrgPermsMixin, SmartTemplateView):
    permission = "orgs.org_home"
    template_name = "spa_frame.haml"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["org"] = self.request.org
        context["is_spa"] = True

        dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
        prefix = "/dev" if dev_mode else settings.STATIC_URL

        # get our list of assets to incude
        scripts = []
        styles = []

        if dev_mode:  # pragma: no cover
            response = requests.get("http://localhost:3000/asset-manifest.json")
            data = response.json()
        else:
            with open("node_modules/@nyaruka/flow-editor/build/asset-manifest.json") as json_file:
                data = json.load(json_file)

        for key, filename in data.get("files").items():

            # tack on our prefix for dev mode
            filename = prefix + filename

            # ignore precache manifest
            if key.startswith("precache-manifest") or key.startswith("service-worker"):
                continue

            # css files
            if key.endswith(".css") and filename.endswith(".css"):
                styles.append(filename)

            # javascript
            if key.endswith(".js") and filename.endswith(".js"):
                scripts.append(filename)

            context["scripts"] = scripts
            context["styles"] = styles
            context["dev_mode"] = dev_mode

        return context

    def has_permission(self, request, *args, **kwargs):
        return not request.user.is_anonymous and request.user.is_beta


class MenuMixin(OrgPermsMixin):
    def create_divider(self):
        return {"type": "divider"}

    def create_space(self):  # pragma: no cover
        return {"type": "space"}

    def create_section(self, name, items=()):  # pragma: no cover
        return {"id": slugify(name), "name": name, "type": "section", "items": items}

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
        endpoint=None,
        href=None,
        count=None,
        perm=None,
        items=[],
        inline=False,
        bottom=False,
        verbose_name=None,
    ):

        if perm and not self.has_org_perm(perm):  # pragma: no cover
            return

        menu_item = {"name": name, "inline": inline}
        menu_item["id"] = menu_id if menu_id else slugify(name)
        menu_item["bottom"] = bottom
        menu_item["verbose_name"] = verbose_name

        if icon:
            menu_item["icon"] = icon

        if count is not None:
            menu_item["count"] = count

        if endpoint:
            if endpoint[0] == "/":  # pragma: no cover
                menu_item["endpoint"] = endpoint
            elif self.has_org_perm(endpoint):
                menu_item["endpoint"] = reverse(endpoint)

        if href:
            if href[0] == "/":
                menu_item["href"] = href
            elif self.has_org_perm(href):
                menu_item["href"] = reverse(href)

        if items:  # pragma: no cover
            menu_item["items"] = items

        # only include the menu item if we have somewhere to go
        if "href" not in menu_item and "endpoint" not in menu_item and not inline:
            return None

        return menu_item

    def get_menu(self):
        menu = [item for item in self.derive_menu() if item is not None]
        return menu

    def render_to_response(self, context, **response_kwargs):
        return JsonResponse({"results": self.get_menu()})


class OrgCRUDL(SmartCRUDL):
    actions = (
        "signup",
        "home",
        "read",
        "token",
        "edit",
        "edit_sub_org",
        "join",
        "join_accept",
        "grant",
        "accounts",
        "create_login",
        "choose",
        "delete",
        "manage_accounts",
        "manage_accounts_sub_org",
        "manage",
        "menu",
        "update",
        "country",
        "languages",
        "twilio_connect",
        "twilio_account",
        "vonage_account",
        "vonage_connect",
        "plan",
        "sub_orgs",
        "create",
        "export",
        "import",
        "plivo_connect",
        "whatsapp_cloud_connect",
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
            self.org = self.request.org

            if self.request.user.is_staff:
                return True
            return super().has_permission(request, *args, **kwargs)

        def derive_menu(self):
            submenu = self.kwargs.get("submenu")

            # how this menu is made up is a wip
            # TODO: remove pragma
            if submenu == "settings":  # pragma: no cover

                menu = []
                menu.append(
                    self.create_menu_item(
                        menu_id="workspace", name=self.org.name, icon="icon.workspace", href="orgs.org_workspace"
                    )
                )

                if self.request.user.settings.two_factor_enabled:
                    menu.append(
                        self.create_menu_item(
                            menu_id="security",
                            name=_("Security"),
                            icon="icon.two_factor_enabled",
                            href=reverse("orgs.user_two_factor_tokens"),
                        )
                    )
                else:
                    menu.append(
                        self.create_menu_item(
                            menu_id="authentication",
                            name=_("Enable 2FA"),
                            icon="icon.two_factor_disabled",
                            href=reverse("orgs.user_two_factor_enable"),
                        )
                    )

                if self.has_org_perm("orgs.org_account"):
                    menu.append(
                        self.create_menu_item(
                            menu_id="account",
                            name=_("Account"),
                            icon="icon.account",
                            href=reverse("orgs.user_account"),
                        )
                    )

                menu.append(self.create_menu_item(name=_("Users"), icon="icon.users", href="orgs.org_manage_accounts"))
                menu.append(
                    self.create_menu_item(name=_("Resthooks"), icon="icon.resthooks", href="orgs.org_resthooks")
                )

                if self.has_org_perm("channels.channel_read"):
                    from temba.channels.views import get_channel_read_url

                    items = []
                    channels = Channel.objects.filter(org=self.org, is_active=True, parent=None).order_by("-role")
                    for channel in channels:
                        icon = channel.type.icon.replace("icon-", "")
                        icon = icon.replace("power-cord", "box")
                        items.append(
                            self.create_menu_item(
                                menu_id=f"{channel.uuid}",
                                name=channel.name,
                                href=get_channel_read_url(channel),
                                icon=icon,
                            )
                        )

                    if len(items):
                        menu.append(self.create_menu_item(name=_("Channels"), items=items, inline=True))

                if self.has_org_perm("classifiers.classifier_read"):
                    items = []
                    classifiers = Classifier.objects.filter(org=self.org, is_active=True).order_by("-created_on")
                    for classifier in classifiers:
                        items.append(
                            self.create_menu_item(
                                menu_id=classifier.uuid,
                                name=classifier.name,
                                href=reverse("classifiers.classifier_read", args=[classifier.uuid]),
                                icon=classifier.get_type().icon.replace("icon-", ""),
                            )
                        )

                    if len(items):
                        menu.append(self.create_menu_item(name=_("Classifiers"), items=items, inline=True))

                if self.has_org_perm("archives.archive_message"):

                    items = [
                        self.create_menu_item(
                            name=_("Messages"),
                            icon="icon.message",
                            href=reverse("archives.archive_message"),
                        ),
                        self.create_menu_item(
                            name=_("Flow Runs"),
                            icon="icon.flow",
                            href=reverse("archives.archive_run"),
                        ),
                    ]

                    menu.append(self.create_menu_item(name=_("Archives"), items=items, inline=True))

                child_orgs = self.org.children.filter(is_active=True).order_by("name")
                if child_orgs:
                    menu.append(self.create_section(_("Workspaces")))

                for child in child_orgs:
                    menu.append(
                        self.create_menu_item(
                            name=child.name,
                            menu_id=child.id,
                            icon="icon.workspace",
                            href=f"{reverse('orgs.org_manage_accounts_sub_org')}?org={child.id}",
                        )
                    )

                return menu

            if submenu == "staff":
                return [
                    self.create_menu_item(
                        menu_id="workspaces",
                        name=_("Workspaces"),
                        icon="icon.workspace",
                        href=reverse("orgs.org_manage"),
                    ),
                    self.create_menu_item(
                        menu_id="users",
                        name=_("Users"),
                        icon="icon.users",
                        href=reverse("orgs.user_list"),
                    ),
                ]

            menu = [
                self.create_menu_item(
                    menu_id="messages", name=_("Messages"), icon="icon.messages", endpoint="msgs.msg_menu"
                ),
                self.create_menu_item(
                    menu_id="contacts", name=_("Contacts"), icon="icon.contacts", endpoint="contacts.contact_menu"
                ),
                self.create_menu_item(menu_id="flows", name=_("Flows"), icon="icon.flows", endpoint="flows.flow_menu"),
                self.create_menu_item(
                    menu_id="triggers", name=_("Triggers"), icon="icon.triggers", endpoint="triggers.trigger_menu"
                ),
                self.create_menu_item(
                    menu_id="campaigns", name=_("Campaigns"), icon="icon.campaigns", endpoint="campaigns.campaign_menu"
                ),
                self.create_menu_item(
                    menu_id="tickets",
                    name=_("Tickets"),
                    icon="icon.tickets",
                    endpoint="tickets.ticket_menu",
                    href="tickets.ticket_list",
                ),
            ]

            if self.org:
                menu.append(
                    {
                        "id": "settings",
                        "name": _("Settings"),
                        "icon": "icon.settings",
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
                        icon="icon.staff",
                        endpoint=f"{reverse('orgs.org_menu')}staff/",
                        bottom=True,
                    )
                )

            return menu

            # Other Plugins:
            # Wit.ai, Luis, Bothub, ZenDesk, DT One, Chatbase, Prometheus, Zapier/Resthooks

    class Import(SpaMixin, NonAtomicMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class FlowImportForm(Form):
            import_file = forms.FileField(help_text=_("The import file"))
            update = forms.BooleanField(help_text=_("Update all flows and campaigns"), required=False)

            def __init__(self, *args, **kwargs):
                self.org = kwargs["org"]
                del kwargs["org"]
                super().__init__(*args, **kwargs)

            def clean_import_file(self):
                # check that it isn't too old
                data = self.cleaned_data["import_file"].read()
                try:
                    json_data = json.loads(force_str(data))
                except (DjangoUnicodeDecodeError, ValueError):
                    raise ValidationError(_("This file is not a valid flow definition file."))

                if Version(str(json_data.get("version", 0))) < Version(Org.EARLIEST_IMPORT_VERSION):
                    raise ValidationError(
                        _("This file is no longer valid. Please export a new version and try again.")
                    )

                return data

        success_message = _("Import successful")
        form_class = FlowImportForm

        def get_success_url(self):  # pragma: needs cover
            return reverse("orgs.org_home")

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.request.org
            return kwargs

        def form_valid(self, form):
            try:
                org = self.request.org
                data = json.loads(form.cleaned_data["import_file"])
                org.import_app(data, self.request.user, self.request.branding["link"])
            except Exception as e:
                # this is an unexpected error, report it to sentry
                logger = logging.getLogger(__name__)
                logger.error("Exception on app import: %s" % str(e), exc_info=True)
                form._errors["import_file"] = form.error_class([_("Sorry, your import file is invalid.")])
                return self.form_invalid(form)

            return super().form_valid(form)  # pragma: needs cover

    class Export(SpaMixin, InferOrgMixin, OrgPermsMixin, SmartTemplateView):
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

            export = org.export_definitions(request.branding["link"], components)
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

            context["flow_id"] = int(self.request.GET.get("flow", 0))
            context["campaign_id"] = int(self.request.GET.get("campaign", 0))

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

    class TwilioConnect(SpaMixin, ComponentFormMixin, ModalMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class TwilioConnectForm(forms.Form):
            account_sid = forms.CharField(help_text=_("Your Twilio Account SID"), widget=InputWidget())
            account_token = forms.CharField(help_text=_("Your Twilio Account Token"), widget=InputWidget())

            def clean(self):
                account_sid = self.cleaned_data.get("account_sid", None)
                account_token = self.cleaned_data.get("account_token", None)

                if not account_sid:  # pragma: needs cover
                    raise ValidationError(_("You must enter your Twilio Account SID"))

                if not account_token:
                    raise ValidationError(_("You must enter your Twilio Account Token"))

                try:
                    client = Client(account_sid, account_token)

                    # get the actual primary auth tokens from twilio and use them
                    account = client.api.account.fetch()
                    self.cleaned_data["account_sid"] = account.sid
                    self.cleaned_data["account_token"] = account.auth_token
                except Exception:
                    raise ValidationError(
                        _("The Twilio account SID and Token seem invalid. Please check them again and retry.")
                    )

                return self.cleaned_data

        form_class = TwilioConnectForm
        submit_button_name = "Save"
        field_config = dict(account_sid=dict(label=""), account_token=dict(label=""))
        success_message = "Twilio Account successfully connected."

        def get_success_url(self):
            claim_type = self.request.GET.get("claim_type", "twilio")

            if claim_type == "twilio_messaging_service":
                return reverse("channels.types.twilio_messaging_service.claim")

            if claim_type == "twilio_whatsapp":
                return reverse("channels.types.twilio_whatsapp.claim")

            if claim_type == "twilio":
                return reverse("channels.types.twilio.claim")

            return reverse("channels.channel_claim")

        def form_valid(self, form):
            account_sid = form.cleaned_data["account_sid"]
            account_token = form.cleaned_data["account_token"]

            org = self.get_object()
            org.connect_twilio(account_sid, account_token, self.request.user)
            org.save()

            return HttpResponseRedirect(self.get_success_url())

    class VonageAccount(InferOrgMixin, ComponentFormMixin, OrgPermsMixin, SmartUpdateView):
        class Form(forms.ModelForm):
            api_key = forms.CharField(max_length=128, label=_("API Key"), required=False)
            api_secret = forms.CharField(max_length=128, label=_("API Secret"), required=False)
            disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=True)

            def clean(self):
                super().clean()
                if self.cleaned_data.get("disconnect", "false") == "false":
                    api_key = self.cleaned_data.get("api_key", None)
                    api_secret = self.cleaned_data.get("api_secret", None)

                    if not api_key:
                        raise ValidationError(_("You must enter your account API Key"))

                    if not api_secret:  # pragma: needs cover
                        raise ValidationError(_("You must enter your account API Secret"))

                    from temba.channels.types.vonage.client import VonageClient

                    if not VonageClient(api_key, api_secret).check_credentials():
                        raise ValidationError(
                            _("Your API key and secret seem invalid. Please check them again and retry.")
                        )

                return self.cleaned_data

            class Meta:
                model = Org
                fields = ("api_key", "api_secret", "disconnect")

        form_class = Form
        success_message = ""

        def derive_initial(self):
            initial = super().derive_initial()
            org = self.get_object()
            config = org.config
            initial["api_key"] = config.get(Org.CONFIG_VONAGE_KEY, "")
            initial["api_secret"] = config.get(Org.CONFIG_VONAGE_SECRET, "")
            initial["disconnect"] = "false"
            return initial

        def form_valid(self, form):
            disconnect = form.cleaned_data.get("disconnect", "false") == "true"
            user = self.request.user
            org = self.request.org

            if disconnect:
                org.remove_vonage_account(user)
                return HttpResponseRedirect(reverse("orgs.org_home"))
            else:
                api_key = form.cleaned_data["api_key"]
                api_secret = form.cleaned_data["api_secret"]

                org.connect_vonage(api_key, api_secret, user)
                return super().form_valid(form)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.get_object()
            client = org.get_vonage_client()
            if client:
                config = org.config
                context["api_key"] = config.get(Org.CONFIG_VONAGE_KEY, "--")

            return context

    class VonageConnect(SpaMixin, ModalMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class Form(forms.Form):
            api_key = forms.CharField(help_text=_("Your Vonage API key"), widget=InputWidget())
            api_secret = forms.CharField(help_text=_("Your Vonage API secret"), widget=InputWidget())

            def clean(self):
                super().clean()

                api_key = self.cleaned_data.get("api_key")
                api_secret = self.cleaned_data.get("api_secret")

                from temba.channels.types.vonage.client import VonageClient

                if not VonageClient(api_key, api_secret).check_credentials():
                    raise ValidationError(
                        _("Your API key and secret seem invalid. Please check them again and retry.")
                    )

                return self.cleaned_data

        form_class = Form
        submit_button_name = "Save"
        success_message = "Vonage Account successfully connected."

        def form_valid(self, form):
            api_key = form.cleaned_data["api_key"]
            api_secret = form.cleaned_data["api_secret"]

            org = self.get_object()

            org.connect_vonage(api_key, api_secret, self.request.user)

            org.save()

            return HttpResponseRedirect(self.get_success_url())

    class Plan(InferOrgMixin, OrgPermsMixin, SmartReadView):
        pass

    class WhatsappCloudConnect(InferOrgMixin, OrgPermsMixin, SmartFormView):
        class WhatsappCloudConnectForm(forms.Form):
            user_access_token = forms.CharField(min_length=32, required=True)

            def clean(self):
                try:
                    auth_token = self.cleaned_data.get("user_access_token", None)

                    app_id = settings.FACEBOOK_APPLICATION_ID
                    app_secret = settings.FACEBOOK_APPLICATION_SECRET

                    url = "https://graph.facebook.com/v13.0/debug_token"
                    params = {"access_token": f"{app_id}|{app_secret}", "input_token": auth_token}

                    response = requests.get(url, params=params)
                    if response.status_code != 200:  # pragma: no cover
                        raise Exception("Failed to debug user token")

                    response_json = response.json()

                    for perm in ["business_management", "whatsapp_business_management", "whatsapp_business_messaging"]:
                        if perm not in response_json.get("data", dict()).get("scopes", []):
                            raise Exception(
                                'Missing permission, we need all the following permissions "business_management", "whatsapp_business_management", "whatsapp_business_messaging"'
                            )
                except Exception:
                    raise forms.ValidationError(
                        _("Sorry account could not be connected. Please try again"), code="invalid"
                    )

                return self.cleaned_data

        form_class = WhatsappCloudConnectForm
        success_url = "@channels.types.whatsapp_cloud.claim"
        field_config = dict(api_key=dict(label=""), api_secret=dict(label=""))

        def pre_process(self, request, *args, **kwargs):
            session_token = self.request.session.get(Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN, None)
            if session_token:
                return HttpResponseRedirect(self.get_success_url())

            return super().pre_process(request, *args, **kwargs)

        def form_valid(self, form):
            auth_token = form.cleaned_data["user_access_token"]

            # add the credentials to the session
            self.request.session[Channel.CONFIG_WHATSAPP_CLOUD_USER_TOKEN] = auth_token
            return HttpResponseRedirect(self.get_success_url())

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["connect_url"] = reverse("orgs.org_whatsapp_cloud_connect")
            context["facebook_app_id"] = settings.FACEBOOK_APPLICATION_ID

            claim_error = None
            if context["form"].errors:
                claim_error = context["form"].errors.get("__all__", [""])[0]
            context["claim_error"] = claim_error

            return context

    class PlivoConnect(ModalMixin, ComponentFormMixin, InferOrgMixin, OrgPermsMixin, SmartFormView):
        class PlivoConnectForm(forms.Form):
            auth_id = forms.CharField(help_text=_("Your Plivo auth ID"))
            auth_token = forms.CharField(help_text=_("Your Plivo auth token"))

            def clean(self):
                super().clean()

                auth_id = self.cleaned_data.get("auth_id", None)
                auth_token = self.cleaned_data.get("auth_token", None)

                headers = http_headers(extra={"Content-Type": "application/json"})

                response = requests.get(
                    "https://api.plivo.com/v1/Account/%s/" % auth_id, headers=headers, auth=(auth_id, auth_token)
                )

                if response.status_code != 200:
                    raise ValidationError(
                        _("Your Plivo auth ID and auth token seem invalid. Please check them again and retry.")
                    )

                return self.cleaned_data

        form_class = PlivoConnectForm
        submit_button_name = "Save"
        success_url = "@channels.types.plivo.claim"
        field_config = dict(auth_id=dict(label=""), auth_token=dict(label=""))
        success_message = "Plivo credentials verified. You can now add a Plivo channel."

        def form_valid(self, form):

            auth_id = form.cleaned_data["auth_id"]
            auth_token = form.cleaned_data["auth_token"]

            # add the credentials to the session
            self.request.session[Channel.CONFIG_PLIVO_AUTH_ID] = auth_id
            self.request.session[Channel.CONFIG_PLIVO_AUTH_TOKEN] = auth_token

            return HttpResponseRedirect(self.get_success_url())

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
                return HttpResponseRedirect(reverse("orgs.org_home"))
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

            if obj.is_flagged:
                menu.add_url_post(_("Unflag"), f"{reverse('orgs.org_update', args=[obj.id])}?action=unflag")
            else:  # pragma: needs cover
                menu.add_url_post(_("Flag"), f"{reverse('orgs.org_update', args=[obj.id])}?action=flag")

            if not obj.is_verified():
                menu.add_url_post(_("Verify"), f"{reverse('orgs.org_update', args=[obj.id])}?action=verify")

            menu.add_modax(
                _("Delete"),
                "delete-org",
                reverse("orgs.org_delete", args=[obj.id]),
                title=_("Delete Workspace"),
                disabled=True,
            )

            menu.new_group()
            menu.add_url_post(
                _("Service"),
                f'{reverse("orgs.org_service")}?organization={obj.id}&redirect_url={reverse("msgs.msg_inbox", args=[])}',
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
        default_order = ("plan_end", "-created_on")
        search_fields = ("name__icontains", "created_by__email__iexact", "config__icontains")
        link_fields = ("name", "owner")
        filters = (
            ("all", _("All"), dict()),
            ("anon", _("Anonymous"), dict(is_anon=True, is_suspended=False)),
            ("flagged", _("Flagged"), dict(is_flagged=True, is_suspended=False)),
            ("suspended", _("Suspended"), dict(is_suspended=True)),
            ("verified", _("Verified"), dict(config__contains='"verified": true', is_suspended=False)),
        )

        @csrf_exempt
        def dispatch(self, *args, **kwargs):
            return super().dispatch(*args, **kwargs)

        def get_owner(self, obj):
            owner = obj.get_owner()
            return f"{owner.name} ({owner.email})"

        def derive_queryset(self, **kwargs):
            obj_filter = self.request.GET.get("filter", "all")

            qs = super().derive_queryset(**kwargs).filter(is_active=True)
            qs = qs.filter(brand=self.request.branding["slug"])

            for filter_key, _, filter_kwargs in self.filters:
                if filter_key == obj_filter:
                    qs = qs.filter(**filter_kwargs)
                    break
            else:
                qs = qs.filter(is_suspended=False)

            return qs

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
                fields = (
                    "name",
                    "features",
                    "is_anon",
                    "is_suspended",
                    "is_flagged",
                )

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
                if action == "flag":
                    self.get_object().flag()
                elif action == "verify":
                    self.get_object().verify()
                elif action == "unflag":
                    self.get_object().unflag()
                return HttpResponseRedirect(self.get_success_url())

            return super().post(request, *args, **kwargs)

        def pre_save(self, obj):
            obj = super().pre_save(obj)

            cleaned_data = self.form.cleaned_data

            obj.limits = cleaned_data["limits"]
            return obj

    class Delete(SpaMixin, OrgObjPermsMixin, ModalMixin, SmartDeleteView):
        cancel_url = "id@orgs.org_update"
        success_url = "@orgs.org_workspace"
        fields = ("id",)
        submit_button_name = _("Delete")

        def has_org_perm(self, codename):
            # users can't delete the primary org
            org = self.get_object()
            if not org.parent:
                return False

            return super().has_org_perm(codename)

        def has_permission(self, request, *args, **kwargs):
            # staff can delete any org
            if request.user.is_staff:
                return True
            return super().has_permission(request, *args, **kwargs)

        def get_object_org(self):
            # child orgs work in the context of their parent
            org = self.get_object()
            return org if not org.parent else org.parent

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["delete_on"] = timezone.now() + timedelta(days=Org.DELETE_DELAY_DAYS)
            return context

        def post(self, request, *args, **kwargs):
            self.object = self.get_object()
            self.object.release(request.user)
            return self.render_modal_response()

    class Accounts(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class PasswordForm(forms.ModelForm):
            surveyor_password = forms.CharField(
                max_length=128, widget=InputWidget(attrs={"placeholder": "Surveyor Password", "widget_only": True})
            )

            def clean_surveyor_password(self):  # pragma: needs cover
                password = self.cleaned_data.get("surveyor_password", "")
                existing = Org.objects.filter(surveyor_password=password).exclude(pk=self.instance.pk).first()
                if existing:
                    raise forms.ValidationError(_("This password is not valid. Choose a new password and try again."))
                return password

            class Meta:
                model = Org
                fields = ("surveyor_password",)

        form_class = PasswordForm
        success_url = "@orgs.org_home"
        success_message = ""
        submit_button_name = _("Save Changes")
        title = "Logins"
        fields = ("surveyor_password",)

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.get_object()
            role_summary = []
            for role in OrgRole:
                num_users = org.get_users(roles=[role]).count()
                if num_users == 1:
                    role_summary.append(f"1 {role.display}")
                elif num_users > 1:
                    role_summary.append(f"{num_users} {role.display_plural}")

            context["role_summary"] = role_summary
            return context

    class ManageAccounts(SpaMixin, ContentMenuMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class AccountsForm(forms.ModelForm):
            invite_emails = forms.CharField(
                required=False, widget=InputWidget(attrs={"widget_only": True, "placeholder": _("Email Address")})
            )
            invite_role = forms.ChoiceField(
                choices=[], required=True, initial="V", label=_("Role"), widget=SelectWidget()
            )

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)

                role_choices = [(r.code, r.display) for r in OrgRole]

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

        def pre_process(self, request, *args, **kwargs):
            if Org.FEATURE_USERS not in request.org.features:
                return HttpResponseRedirect(reverse("orgs.org_home"))

        def derive_title(self):
            if self.object.parent and self.is_spa():
                return self.object.name
            else:
                return super().derive_title()

        def build_content_menu(self, menu):
            other_org = self.request.org.id != self.get_object().id

            if self.is_spa():
                if other_org:
                    menu.add_modax(
                        _("Edit"),
                        "edit-workspace",
                        f"{reverse('orgs.org_edit_sub_org')}?org={self.object.id}",
                        title=_("Edit Workspace"),
                    )

                    if self.has_org_perm("orgs.org_delete"):
                        menu.add_modax(
                            _("Delete"),
                            "delete-workspace",
                            reverse("orgs.org_delete", args=[self.object.id]),
                            title=_("Delete Workspace"),
                            disabled=True,
                        )

            else:
                if other_org:
                    menu.add_link(_("Workspaces"), reverse("orgs.org_sub_orgs"))

                menu.add_link(_("Home"), reverse("orgs.org_home"))

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["org"] = self.get_object()
            return kwargs

        def post_save(self, obj):
            obj = super().post_save(obj)

            cleaned_data = self.form.cleaned_data
            org = self.get_object()

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
                elif org.get_user_role(user) != new_role:
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
            still_in_org = self.get_object().has_user(self.request.user)

            # if current user no longer belongs to this org, redirect to org chooser
            return reverse("orgs.org_manage_accounts") if still_in_org else reverse("orgs.org_choose")

    class ManageAccountsSubOrg(ManageAccounts):
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
            organization = TembaChoiceField(queryset=Org.objects.all(), empty_label=None)
            redirect_url = forms.CharField(required=False)

        form_class = ServiceForm
        fields = ("organization", "redirect_url")

        # valid form means we set our org and redirect to their inbox
        def form_valid(self, form):
            switch_to_org(self.request, form.cleaned_data["organization"])
            success_url = form.cleaned_data["redirect_url"] or reverse("msgs.msg_inbox")
            return HttpResponseRedirect(success_url)

        # invalid form login 'logs out' the user from the org and takes them to the org manage page
        def form_invalid(self, form):
            switch_to_org(self.request, None)
            return HttpResponseRedirect(reverse("orgs.org_manage"))

    class SubOrgs(SpaMixin, ContentMenuMixin, OrgPermsMixin, InferOrgMixin, SmartListView):
        fields = ("name", "contacts", "manage", "created_on")
        title = _("Workspaces")
        link_fields = []

        def build_content_menu(self, menu):
            if self.has_org_perm("orgs.org_dashboard"):
                menu.add_link(_("Dashboard"), reverse("dashboard.dashboard_home"))

        def get_manage(self, obj):  # pragma: needs cover
            if obj == self.get_object():
                return mark_safe(
                    f'<a href="{reverse("orgs.org_manage_accounts")}" class="float-right pr-4"><div class="button-light inline-block ">{_("Manage Logins")}</div></a>'
                )

            if obj.parent:
                return mark_safe(
                    f'<a href="{reverse("orgs.org_manage_accounts_sub_org")}?org={obj.id}" class="float-right pr-4"><div class="button-light inline-block">{_("Manage Logins")}</div></a>'
                )
            return ""

        def get_contacts(self, obj):
            return obj.get_contact_count()

        def get_name(self, obj):
            org_type = "child"
            if not obj.parent:
                org_type = "parent"
            if self.has_org_perm("orgs.org_edit_sub_org") and obj.parent:  # pragma: needs cover
                return mark_safe(
                    f"<temba-modax header={_('Update')} endpoint={reverse('orgs.org_edit_sub_org')}?org={obj.id} ><div class='{org_type}-org-name linked'>{escape(obj.name)}</div><div class='org-timezone'>{obj.timezone}</div></temba-modax>"
                )
            return mark_safe(
                f"<div class='org-name'>{escape(obj.name)}</div><div class='org-timezone'>{obj.timezone}</div>"
            )

        def derive_queryset(self, **kwargs):
            queryset = super().derive_queryset(**kwargs)

            # all our children and ourselves
            org = self.get_object()
            ids = [child.id for child in Org.objects.filter(parent=org)]
            ids.append(org.id)

            return queryset.filter(id__in=ids, is_active=True).order_by("-parent", "name")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["searches"] = ["Nyaruka"]
            return context

        def get_created_by(self, obj):  # pragma: needs cover
            return "%s %s - %s" % (obj.created_by.first_name, obj.created_by.last_name, obj.created_by.email)

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
                return HttpResponseRedirect(reverse("orgs.org_home"))

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
            if self.object.parent:
                if self.is_spa():
                    return f"{reverse('orgs.org_manage_accounts_sub_org')}?org={self.object.id}"
                else:
                    return reverse("orgs.org_sub_orgs")

            # if we created a new separate org, switch to it
            switch_to_org(self.request, self.object)
            return reverse("msgs.msg_inbox")

        def form_valid(self, form):
            default_type = form.TYPE_CHILD if Org.FEATURE_CHILD_ORGS in self.request.org.features else form.TYPE_NEW

            self.object = self.org.create_new(
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

    class Choose(SpaMixin, SmartFormView):
        class Form(forms.Form):
            organization = forms.ModelChoiceField(queryset=Org.objects.none(), empty_label=None)

            def __init__(self, orgs, *args, **kwargs):
                super().__init__(*args, **kwargs)

                self.fields["organization"].queryset = orgs

        form_class = Form
        fields = ("organization",)
        title = _("Select your Workspace")
        success_urls = {
            OrgRole.ADMINISTRATOR: "msgs.msg_inbox",
            OrgRole.EDITOR: "msgs.msg_inbox",
            OrgRole.VIEWER: "msgs.msg_inbox",
            OrgRole.AGENT: "tickets.ticket_list",
            OrgRole.SURVEYOR: "orgs.org_surveyor",
        }

        def get_user_orgs(self):
            return self.request.user.get_orgs(brand=self.request.branding["slug"])

        def get_success_url(self, org):
            role = org.get_user_role(self.request.user)
            return reverse(self.success_urls[role])

        def pre_process(self, request, *args, **kwargs):
            user = self.request.user
            if user.is_authenticated:
                user_orgs = self.get_user_orgs()
                if user_orgs.count() == 1:
                    org = user_orgs[0]
                    switch_to_org(self.request, org)
                    analytics.identify(user, self.request.branding, org)

                    return HttpResponseRedirect(self.get_success_url(org))

                elif user_orgs.count() == 0:
                    if user.is_staff:
                        return HttpResponseRedirect(reverse("orgs.org_manage"))

                    # for regular users, if there's no orgs, log them out with a message
                    messages.info(request, _("No organizations for this account, please contact your administrator."))
                    logout(request)
                    return HttpResponseRedirect(reverse("users.user_login"))
            return None

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["orgs"] = self.get_user_orgs()
            return context

        def get_form_kwargs(self):
            kwargs = super().get_form_kwargs()
            kwargs["orgs"] = self.get_user_orgs()
            return kwargs

        def has_permission(self, request, *args, **kwargs):
            return self.request.user.is_authenticated

        def form_valid(self, form):
            org = form.cleaned_data["organization"]
            switch_to_org(self.request, org)
            analytics.identify(self.request.user, self.request.branding, org)
            return HttpResponseRedirect(self.get_success_url(org))

    class CreateLogin(SmartUpdateView):
        title = ""
        form_class = OrgSignupForm
        fields = ("first_name", "last_name", "password")
        success_message = ""
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Create")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            org = self.get_object()
            if not org:
                messages.info(
                    request, _("Your invitation link is invalid. Please contact your workspace administrator.")
                )
                return HttpResponseRedirect(reverse("public.public_index"))

            invite = self.get_invitation()
            secret = self.kwargs.get("secret")
            has_user = User.objects.filter(username=invite.email).exists()
            if has_user:
                return HttpResponseRedirect(reverse("orgs.org_join_accept", args=[secret]))

            return None

        def pre_save(self, obj):
            obj = super().pre_save(obj)
            self.invitation = self.get_invitation()
            email = self.invitation.email

            user = User.create(
                email,
                self.form.cleaned_data["first_name"],
                self.form.cleaned_data["last_name"],
                password=self.form.cleaned_data["password"],
                language=obj.language,
            )

            # log the user in
            user = authenticate(username=user.username, password=self.form.cleaned_data["password"])
            login(self.request, user)

            role = OrgRole.from_code(self.invitation.user_group) or OrgRole.VIEWER
            obj.add_user(user, role)

            # make the invitation inactive
            self.invitation.is_active = False
            self.invitation.save()

            return obj

        def get_success_url(self):
            if self.invitation.user_group == "S":
                return reverse("orgs.org_surveyor")
            return super().get_success_url()

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<secret>\w+)/$" % (path, action)

        def get_invitation(self, **kwargs):
            secret = self.kwargs.get("secret")
            return Invitation.objects.filter(secret=secret, is_active=True).first()

        def get_object(self, **kwargs):
            invitation = self.get_invitation()
            if invitation:
                return invitation.org
            return None  # pragma: needs cover

        def derive_title(self):
            org = self.get_object()
            return _("Join %(name)s") % {"name": org.name}

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["secret"] = self.kwargs.get("secret")
            context["org"] = self.get_object()
            invitation = self.get_invitation()
            context["email"] = invitation.email

            return context

    class Join(SmartTemplateView):
        title = _("Sign in with your account to accept the invitation")
        permission = False

        def pre_process(self, request, *args, **kwargs):
            secret = self.kwargs.get("secret")

            invite = self.get_invitation()
            if invite:
                has_user = User.objects.filter(username=invite.email).exists()
                if has_user and invite.email == request.user.username:
                    return HttpResponseRedirect(reverse("orgs.org_join_accept", args=[secret]))

                logout(request)
                if not has_user:
                    return HttpResponseRedirect(reverse("orgs.org_create_login", args=[secret]))

            else:
                messages.info(
                    request, _("Your invitation link has expired. Please contact your workspace administrator.")
                )
                return HttpResponseRedirect(reverse("users.user_login"))

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            context["secret"] = self.kwargs.get("secret")
            invitation = self.get_invitation()
            context["email"] = invitation.email

            return context

        def get_invitation(self, **kwargs):  # pragma: needs cover
            secret = self.kwargs.get("secret")
            return Invitation.objects.filter(secret=secret, is_active=True).first()

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<secret>\w+)/$" % (path, action)

    class JoinAccept(SmartUpdateView):
        class JoinAcceptForm(forms.ModelForm):
            class Meta:
                model = Org
                fields = ()

        success_message = ""
        title = ""
        form_class = JoinAcceptForm
        success_url = "@msgs.msg_inbox"
        submit_button_name = _("Join")

        def has_permission(self, request, *args, **kwargs):
            return request.user.is_authenticated

        def pre_process(self, request, *args, **kwargs):
            org = self.get_object()
            invitation = self.get_invitation()
            if not (invitation and org):
                messages.info(
                    request, _("Your invitation link has expired. Please contact your workspace administrator.")
                )
                return HttpResponseRedirect(reverse("public.public_index"))

            secret = self.kwargs.get("secret")

            invitation_email = invitation.email
            has_user = User.objects.filter(username=invitation_email).exists()
            if has_user and invitation_email != request.user.username:
                logout(request)
                return HttpResponseRedirect(reverse("orgs.org_join", args=[secret]))

            return None

        def derive_title(self):  # pragma: needs cover
            org = self.get_object()
            return _("Join %(name)s") % {"name": org.name}

        def save(self, org):  # pragma: needs cover
            org = self.get_object()
            self.invitation = self.get_invitation()
            if org:
                role = OrgRole.from_code(self.invitation.user_group) or OrgRole.VIEWER
                org.add_user(self.request.user, role)

                # make the invitation inactive
                self.invitation.is_active = False
                self.invitation.save()

                switch_to_org(self.request, org)

        def get_success_url(self):  # pragma: needs cover
            if self.invitation.user_group == "S":
                return reverse("orgs.org_surveyor")

            return super().get_success_url()

        @classmethod
        def derive_url_pattern(cls, path, action):
            return r"^%s/%s/(?P<secret>\w+)/$" % (path, action)

        def get_invitation(self, **kwargs):  # pragma: needs cover
            secret = self.kwargs.get("secret")
            return Invitation.objects.filter(secret=secret, is_active=True).first()

        def get_object(self, **kwargs):  # pragma: needs cover
            invitation = self.get_invitation()
            if invitation:
                return invitation.org

        def get_context_data(self, **kwargs):  # pragma: needs cover
            context = super().get_context_data(**kwargs)

            context["org"] = self.get_object()
            return context

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
                return ["orgs/org_surveyor_mobile.haml"]
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
                self.request.user,
                self.request.branding,
                self.form.cleaned_data["name"],
                self.form.cleaned_data["timezone"],
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
            if not request.branding.get("allow_signups", False):  # pragma: needs cover
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

            self.object = Org.create(
                new_user,
                self.request.branding,
                self.form.cleaned_data["name"],
                self.form.cleaned_data["timezone"],
            )

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

    class Token(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class TokenForm(forms.ModelForm):
            class Meta:
                model = Org
                fields = ("id",)

        form_class = TokenForm
        success_url = "@orgs.org_home"
        success_message = ""

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            context["api_token"] = self.request.user.get_api_token(self.request.org)
            return context

    class Prometheus(InferOrgMixin, OrgPermsMixin, SmartUpdateView):
        class ToggleForm(forms.ModelForm):
            class Meta:
                model = Org
                fields = ("id",)

        form_class = ToggleForm
        success_url = "@orgs.org_home"
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

        def build_content_menu(self, menu):
            menu.add_link(_("New Channel"), reverse("channels.channel_claim"), as_button=True)

            if self.has_org_perm("classifiers.classifier_connect"):
                menu.add_link(_("New Classifier"), reverse("classifiers.classifier_connect"))
            if self.has_org_perm("tickets.ticketer_connect") and "ticketers" in settings.FEATURES:
                menu.add_link(_("New Ticketing Service"), reverse("tickets.ticketer_connect"))

            menu.new_group()

            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export"), reverse("orgs.org_export"))

            if self.has_org_perm("orgs.org_import"):
                menu.add_link(_("Import"), reverse("orgs.org_import"))

            menu.new_group()
            menu.add_link(_("Sign Out"), f"{reverse('users.user_logout')}?next={reverse('users.user_login')}")

        def derive_formax_sections(self, formax, context):
            org = self.request.org

            if self.has_org_perm("orgs.org_edit"):
                formax.add_section("org", reverse("orgs.org_edit"), icon="icon-office")

            twilio_client = org.get_twilio_client()
            if twilio_client:  # pragma: needs cover
                formax.add_section("twilio", reverse("orgs.org_twilio_account"), icon="icon-channel-twilio")

            vonage_client = org.get_vonage_client()
            if vonage_client:  # pragma: needs cover
                formax.add_section("vonage", reverse("orgs.org_vonage_account"), icon="icon-vonage")

            if self.has_org_perm("orgs.org_accounts") and Org.FEATURE_USERS in org.features:
                formax.add_section("accounts", reverse("orgs.org_accounts"), icon="icon-users")

            if self.has_org_perm("orgs.org_languages"):
                formax.add_section("languages", reverse("orgs.org_languages"), icon="icon-language")

            if self.has_org_perm("orgs.org_country") and "locations" in settings.FEATURES:
                formax.add_section("country", reverse("orgs.org_country"), icon="icon-location2")

            if self.has_org_perm("orgs.org_smtp_server"):
                formax.add_section("email", reverse("orgs.org_smtp_server"), icon="icon-envelop")

            if self.has_org_perm("orgs.org_token"):
                formax.add_section("token", reverse("orgs.org_token"), icon="icon-cloud-upload", nobutton=True)

            if self.has_org_perm("orgs.org_prometheus"):
                formax.add_section("prometheus", reverse("orgs.org_prometheus"), icon="icon-prometheus", nobutton=True)

            if self.has_org_perm("orgs.org_manage_integrations"):
                for integration in IntegrationType.get_all():
                    if integration.is_available_to(self.request.user):
                        integration.management_ui(self.object, formax)

    class Home(SpaMixin, FormaxMixin, ContentMenuMixin, InferOrgMixin, OrgPermsMixin, SmartReadView):
        title = _("Your Account")

        def build_content_menu(self, menu):
            if self.has_org_perm("channels.channel_claim"):
                menu.add_link(_("Add Channel"), reverse("channels.channel_claim"), as_button=True)
            if self.has_org_perm("classifiers.classifier_connect"):
                menu.add_link(_("Add Classifier"), reverse("classifiers.classifier_connect"))
            if self.has_org_perm("tickets.ticketer_connect") and "ticketers" in settings.FEATURES:
                menu.add_link(_("Add Ticketing Service"), reverse("tickets.ticketer_connect"))

            menu.new_group()

            if self.has_org_perm("orgs.org_export"):
                menu.add_link(_("Export"), reverse("orgs.org_export"))

            if self.has_org_perm("orgs.org_import"):
                menu.add_link(_("Import"), reverse("orgs.org_import"))

            if settings.HELP_URL:  # pragma: needs cover
                menu.new_group()
                menu.add_link(_("Help"), settings.HELP_URL)

            menu.new_group()
            menu.add_link(_("Sign Out"), f"{reverse('users.user_logout')}?next={reverse('users.user_login')}")

        def get_context_data(self, *args, **kwargs):
            context = super().get_context_data(*args, **kwargs)
            # context['channels'] = Channel.objects.filter(org=self.request.org, is_active=True, parent=None).order_by("-role")
            return context

        def add_channel_section(self, formax, channel):

            if self.has_org_perm("channels.channel_read"):
                from temba.channels.views import get_channel_read_url

                formax.add_section("channel", get_channel_read_url(channel), icon=channel.type.icon, action="link")

        def add_classifier_section(self, formax, classifier):

            if self.has_org_perm("classifiers.classifier_read"):
                formax.add_section(
                    "classifier",
                    reverse("classifiers.classifier_read", args=[classifier.uuid]),
                    icon=classifier.get_type().icon,
                    action="link",
                )

        def derive_formax_sections(self, formax, context):
            # add the channel option if we have one
            user = self.request.user
            org = self.request.org

            if org.plan:
                if self.has_org_perm("orgs.org_plan"):
                    formax.add_section("plan", reverse("orgs.org_plan"), icon="icon-credit", action="summary")

            if self.has_org_perm("channels.channel_update"):
                # get any channel thats not a delegate
                channels = Channel.objects.filter(org=org, is_active=True, parent=None).order_by("-role")
                for channel in channels:
                    self.add_channel_section(formax, channel)

                twilio_client = org.get_twilio_client()
                if twilio_client:  # pragma: needs cover
                    formax.add_section("twilio", reverse("orgs.org_twilio_account"), icon="icon-channel-twilio")

                vonage_client = org.get_vonage_client()
                if vonage_client:  # pragma: needs cover
                    formax.add_section("vonage", reverse("orgs.org_vonage_account"), icon="icon-vonage")

            if self.has_org_perm("classifiers.classifier_read"):
                classifiers = org.classifiers.filter(is_active=True).order_by("created_on")
                for classifier in classifiers:
                    self.add_classifier_section(formax, classifier)

            if self.has_org_perm("tickets.ticketer_read"):
                from temba.tickets.types.internal import InternalType

                ext_ticketers = (
                    org.ticketers.filter(is_active=True)
                    .exclude(ticketer_type=InternalType.slug)
                    .order_by("created_on")
                )
                for ticketer in ext_ticketers:
                    formax.add_section(
                        "tickets",
                        reverse("tickets.ticketer_read", args=[ticketer.uuid]),
                        icon=ticketer.type.icon,
                    )

            if self.has_org_perm("orgs.org_profile"):
                formax.add_section("user", reverse("orgs.user_edit"), icon="icon-user", action="redirect")

            if self.has_org_perm("orgs.org_edit"):
                formax.add_section("org", reverse("orgs.org_edit"), icon="icon-office")

            if self.has_org_perm("orgs.org_accounts") and Org.FEATURE_USERS in org.features:
                formax.add_section("accounts", reverse("orgs.org_accounts"), icon="icon-users", action="redirect")

            if self.has_org_perm("orgs.org_languages"):
                formax.add_section("languages", reverse("orgs.org_languages"), icon="icon-language")

            if self.has_org_perm("orgs.org_country") and "locations" in settings.FEATURES:
                formax.add_section("country", reverse("orgs.org_country"), icon="icon-location2")

            if self.has_org_perm("orgs.org_smtp_server"):
                formax.add_section("email", reverse("orgs.org_smtp_server"), icon="icon-envelop")

            if self.has_org_perm("orgs.org_manage_integrations"):
                for integration in IntegrationType.get_all():
                    if integration.is_available_to(user):
                        integration.management_ui(self.object, formax)

            if self.has_org_perm("orgs.org_token"):
                formax.add_section("token", reverse("orgs.org_token"), icon="icon-cloud-upload", nobutton=True)

            if self.has_org_perm("orgs.org_prometheus"):
                formax.add_section("prometheus", reverse("orgs.org_prometheus"), icon="icon-prometheus", nobutton=True)

            if self.has_org_perm("orgs.org_resthooks"):
                formax.add_section(
                    "resthooks",
                    reverse("orgs.org_resthooks"),
                    icon="icon-cloud-lightning",
                    wide="true",
                )

            if self.has_org_perm("orgs.org_two_factor"):
                if user.settings.two_factor_enabled:
                    formax.add_section(
                        "two_factor", reverse("orgs.user_two_factor_tokens"), icon="icon-two-factor", action="link"
                    )
                else:
                    formax.add_section(
                        "two_factor", reverse("orgs.user_two_factor_enable"), icon="icon-two-factor", action="link"
                    )

            # show globals and archives
            formax.add_section("globals", reverse("globals.global_list"), icon="icon-global", action="link")
            formax.add_section("archives", reverse("archives.archive_message"), icon="icon-box", action="link")

    class TwilioAccount(ComponentFormMixin, InferOrgMixin, OrgPermsMixin, SmartUpdateView):

        success_message = ""

        class TwilioKeys(forms.ModelForm):
            account_sid = forms.CharField(max_length=128, label=_("Account SID"), required=False)
            account_token = forms.CharField(max_length=128, label=_("Account Token"), required=False)
            disconnect = forms.CharField(widget=forms.HiddenInput, max_length=6, required=True)

            def clean(self):
                super().clean()
                if self.cleaned_data.get("disconnect", "false") == "false":
                    account_sid = self.cleaned_data.get("account_sid", None)
                    account_token = self.cleaned_data.get("account_token", None)

                    if not account_sid:
                        raise ValidationError(_("You must enter your Twilio Account SID"))

                    if not account_token:  # pragma: needs cover
                        raise ValidationError(_("You must enter your Twilio Account Token"))

                    try:
                        client = Client(account_sid, account_token)

                        # get the actual primary auth tokens from twilio and use them
                        account = client.api.account.fetch()
                        self.cleaned_data["account_sid"] = account.sid
                        self.cleaned_data["account_token"] = account.auth_token
                    except Exception:  # pragma: needs cover
                        raise ValidationError(
                            _("The Twilio account SID and Token seem invalid. Please check them again and retry.")
                        )

                return self.cleaned_data

            class Meta:
                model = Org
                fields = ("account_sid", "account_token", "disconnect")

        form_class = TwilioKeys

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)
            client = self.object.get_twilio_client()
            if client:
                account_sid = client.auth[0]
                sid_length = len(account_sid)
                context["account_sid"] = "%s%s" % ("\u066D" * (sid_length - 16), account_sid[-16:])
            return context

        def derive_initial(self):
            initial = super().derive_initial()
            config = self.object.config
            initial["account_sid"] = config[Org.CONFIG_TWILIO_SID]
            initial["account_token"] = config[Org.CONFIG_TWILIO_TOKEN]
            initial["disconnect"] = "false"
            return initial

        def form_valid(self, form):
            disconnect = form.cleaned_data.get("disconnect", "false") == "true"
            user = self.request.user
            org = self.request.org

            if disconnect:
                org.remove_twilio_account(user)
                return HttpResponseRedirect(reverse("orgs.org_home"))
            else:
                account_sid = form.cleaned_data["account_sid"]
                account_token = form.cleaned_data["account_token"]

                org.connect_twilio(account_sid, account_token, user)
                return super().form_valid(form)

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

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.has_org_perm("orgs.org_edit")

        def get_context_data(self, **kwargs):
            context = super().get_context_data(**kwargs)

            org = self.get_object()

            context["sub_orgs"] = org.children.filter(is_active=True)
            context["is_spa"] = "HTTP_TEMBA_SPA" in self.request.META
            return context

    class EditSubOrg(SpaMixin, ModalMixin, Edit):
        success_url = "@orgs.org_sub_orgs"

        def get_success_url(self):
            if self.is_spa():
                org_id = self.request.GET.get("org")
                return f"{reverse('orgs.org_manage_accounts_sub_org')}?org={org_id}"

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

        def has_permission(self, request, *args, **kwargs):
            self.org = self.derive_org()
            return self.request.user.has_perm("orgs.org_country") or self.has_org_perm("orgs.org_country")

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

            def __init__(self, org, *args, **kwargs):
                super().__init__(*args, **kwargs)
                self.org = org

            class Meta:
                model = Org
                fields = ("primary_lang", "other_langs")

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
            self.org = self.derive_org()
            return self.request.user.has_perm("orgs.org_country") or self.has_org_perm("orgs.org_country")
