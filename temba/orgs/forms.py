import smtplib
from email.utils import parseaddr

from django import forms
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.utils.translation import gettext_lazy as _

from temba.utils.email import EmailSender, is_valid_address, make_smtp_url, parse_smtp_url
from temba.utils.fields import InputWidget
from temba.utils.timezones import TimeZoneFormField

from .models import Org, User


class SignupForm(forms.ModelForm):
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


class SMTPForm(forms.Form):
    from_email = forms.CharField(
        max_length=128,
        label=_("From Address"),
        help_text=_("Can contain a name e.g. Jane Doe <jane@example.org>"),
        widget=InputWidget(),
    )
    host = forms.CharField(
        label=_("Hostname"), max_length=128, widget=InputWidget(attrs={"placeholder": _("smtp.example.com")})
    )
    port = forms.IntegerField(
        label=_("Port"), min_value=1, max_value=65535, widget=InputWidget(attrs={"placeholder": _("25")})
    )
    username = forms.CharField(max_length=128, label=_("Username"), widget=InputWidget())
    password = forms.CharField(max_length=128, label=_("Password"), widget=InputWidget(attrs={"password": True}))

    def __init__(self, org, initial: str, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.org = org

        host, port, username, password, from_email, _ = parse_smtp_url(initial)
        self.fields["from_email"].initial = from_email
        self.fields["host"].initial = host
        self.fields["port"].initial = port
        self.fields["username"].initial = username
        self.fields["password"].initial = password

    def clean_from_email(self):
        data = self.cleaned_data["from_email"]
        if data and not is_valid_address(parseaddr(data)[1]):
            raise forms.ValidationError(_("Not a valid email address."))
        return data

    def clean(self):
        super().clean()

        # if individual fields look valid, do an actual email test...
        if self.is_valid():
            from_email = self.cleaned_data["from_email"]
            host = self.cleaned_data["host"]
            port = self.cleaned_data["port"]
            username = self.cleaned_data["username"]
            password = self.cleaned_data["password"]

            smtp_url = make_smtp_url(host, port, username, password, from_email, tls=True)
            sender = EmailSender.from_smtp_url(self.org.branding, smtp_url)
            recipients = [admin.email for admin in self.org.get_admins().order_by("email")]
            subject = _("%(name)s SMTP settings test") % self.org.branding
            try:
                sender.send(recipients, subject, "orgs/email/smtp_test", {})
            except smtplib.SMTPException as e:
                raise ValidationError(_("SMTP settings test failed with error: %s") % str(e))
            except Exception:
                raise ValidationError(_("SMTP settings test failed."))

            self.cleaned_data["smtp_url"] = smtp_url

        return self.cleaned_data

    class Meta:
        fields = ("from_email", "host", "username", "password", "port")
