import pyotp
from smartmin.users.views import Login
from smartmin.views import SmartFormView

from django import forms
from django.contrib.auth import login
from django.contrib.auth.models import User
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.translation import ugettext_lazy as _

from temba.orgs.models import BackupToken


class LoginView(Login):
    def form_valid(self, form):
        user = form.get_user()
        settings = user.get_settings()

        if settings.two_factor_enabled:
            self.request.session["user_pk"] = user.pk

            next_url = self.get_redirect_url()
            query_string = "?{}={}".format(self.redirect_field_name, next_url) if next_url else ""
            return redirect("{}{}".format(reverse("two_factor.token"), query_string))
        else:
            login(self.request, user)

        return super().form_valid(form)


class TokenView(SmartFormView):
    class TokenForm(forms.Form):
        token = forms.CharField(
            label=_("Authentication Token"),
            help_text=_("Enter the code from your authentication application"),
            strip=True,
            required=True,
        )

        def __init__(self, *args, **kwargs):
            self.request = kwargs.pop("request")
            super().__init__(*args, **kwargs)

        def clean_token(self):
            token = self.cleaned_data.get("token", None)
            user_pk = self.request.session.get("user_pk", None)
            user = User.objects.get(pk=user_pk)
            totp = pyotp.TOTP(user.get_settings().otp_secret)
            token_valid = totp.verify(token, valid_window=2)
            settings = user.get_settings()

            if not user_pk:
                raise forms.ValidationError("Login session expired. Please try again.", code="expired")

            if not settings.two_factor_enabled:
                raise forms.ValidationError("MFA not enabled for this user. Please try again.", code="mfa-disabled")

            if not token_valid:
                raise forms.ValidationError(_("Invalid MFA token. Please try again."), code="invalid-token")
            return token

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        user_pk = self.request.session.get("user_pk", None)
        user = User.objects.get(pk=user_pk)
        login(self.request, user)
        return super().form_valid(form)

    form_class = TokenForm
    fields = ("token",)
    success_url = "@orgs.org_choose"
    template_name = "two_factor/token.html"
    submit_button_name = _("Save")
    title = "Two Factor Authentication"


class BackupTokenView(SmartFormView):
    class BackupTokenForm(forms.Form):
        backup_token = forms.CharField(
            label=_("Backup Token"),
            help_text=_("Enter one of the tokens generated when activating the two-factor login"),
            strip=True,
            required=True,
        )

        def __init__(self, *args, **kwargs):
            self.request = kwargs.pop("request")
            super().__init__(*args, **kwargs)

        def clean_backup_token(self):
            token = self.cleaned_data.get("backup_token", None)
            user_pk = self.request.session.get("user_pk", None)
            user = User.objects.get(pk=user_pk)
            settings = user.get_settings()
            try:
                backup_token = BackupToken.objects.get(settings__user=user, token=token)
            except BackupToken.DoesNotExist:
                raise forms.ValidationError("This backup token does not exist.", code="not-exist")

            if backup_token.used:
                raise forms.ValidationError("This token has already been used.", code="used")

            if not user_pk:
                raise forms.ValidationError("Login session expired. Please try again.", code="expired")

            if not settings.two_factor_enabled:
                raise forms.ValidationError("MFA not enabled for this user. Please try again.", code="mfa-disabled")

            return token

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["request"] = self.request
        return kwargs

    def form_valid(self, form):
        token = form.cleaned_data.get("backup_token", None)
        user_pk = self.request.session.get("user_pk", None)
        user = User.objects.get(pk=user_pk)
        backup = BackupToken.objects.get(settings__user=user, token=token)
        backup.used = True
        backup.save()
        login(self.request, user)
        return super().form_valid(form)

    form_class = BackupTokenForm
    fields = ("backup_token",)
    success_url = "@orgs.org_choose"
    template_name = "two_factor/backup_tokens.html"
    submit_button_name = _("Save")
    title = "Backup Tokens"
