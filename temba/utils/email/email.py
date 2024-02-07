import re
from urllib.parse import parse_qs, quote, unquote, urlencode, urlparse

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, get_connection, send_mail as django_send_email
from django.core.validators import EmailValidator
from django.template import loader
from django.utils import timezone

from temba.utils import get_nested_key


class TembaEmailValidator(EmailValidator):
    user_regex = re.compile(
        r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*\Z"  # dot-atom
        r'|^"([\001-\010\013\014\016-\[\]-\177]|\\[\001-\011\013\014\016-\177])*"\Z)',  # quoted-string
        re.IGNORECASE,
    )
    domain_regex = re.compile(
        # max length for domain name labels is 63 characters per RFC 1034
        r"(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,63}|[A-Z0-9-]{2,}(?<!-))\Z",
        re.IGNORECASE,
    )


temba_validate_email = TembaEmailValidator()


def is_valid_address(address):
    """
    Very loose email address check
    """
    if not address:
        return False

    try:
        temba_validate_email(address)
    except ValidationError:
        return False

    return True


def make_smtp_url(host: str, port: int, username: str, password: str, from_email: str, tls: bool) -> str:
    username = quote(username)
    password = quote(password, safe="")
    params = {}
    if from_email:
        params["from"] = f"{from_email.strip()}"
    if tls:
        params["tls"] = "true"

    url = f"smtp://{username}:{password}@{host}:{port}/"
    if params:
        url += f"?{urlencode(params)}"
    return url


def parse_smtp_url(smtp_url: str) -> tuple:
    parsed = urlparse(smtp_url or "")
    tls_param = parse_qs(parsed.query).get("tls")
    from_param = parse_qs(parsed.query).get("from")

    return (
        parsed.hostname,
        parsed.port or 25,
        unquote(parsed.username) if parsed.username else None,
        unquote(parsed.password) if parsed.username else None,
        from_param[0] if from_param else None,
        tls_param[0] == "true" if tls_param else False,
    )


def send_custom_smtp_email(
    recipients: list,
    subject: str,
    text: str,
    host: str,
    port: int,
    username: str,
    password: str,
    from_email: str,
    use_tls: bool,
):
    """
    Sends a text email to the given recipients using the given SMTP configuration.
    """

    connection = get_connection(
        None,
        fail_silently=False,
        host=host,
        port=port,
        username=username,
        password=password,
        use_tls=use_tls,
    )

    send_email(recipients, subject, text, None, from_email, connection=connection)


def send_template_email(recipients: list, subject: str, template: str, context: dict, branding: dict):
    """
    Sends a multi-part email rendered from templates for the text and html parts. `template` should be the name of the
    template, without .html or .txt (e.g. 'channels/email/power_charging').
    """

    # brands are allowed to give us a from address
    from_email = get_nested_key(
        branding, "emails.notifications", getattr(settings, "DEFAULT_FROM_EMAIL", "website@rapidpro.io")
    )

    html_template = loader.get_template(template + ".html")
    text_template = loader.get_template(template + ".txt")

    context["subject"] = subject
    context["branding"] = branding
    context["now"] = timezone.now()

    html = html_template.render(context)
    text = text_template.render(context)

    send_email(recipients, subject, text, html, from_email)


def send_email(recipients: list, subject: str, text: str, html: str, from_email: str, connection=None):
    """
    Actually sends the email. Having this as separate function makes testing multi-part emails easier
    """
    if settings.SEND_EMAILS:
        if html is not None:
            message = EmailMultiAlternatives(subject, text, from_email, recipients, connection=connection)
            message.attach_alternative(html, "text/html")
            message.send()
        else:
            django_send_email(subject, text, from_email, recipients, connection=connection)
    else:  # pragma: no cover
        # just print to console if we aren't meant to send emails
        print("------------- Skipping sending email, SEND_EMAILS is False -------------")
        print(f"To: {', '.join(recipients)}")
        print(f"From: {from_email}")
        print(f"Subject: {subject}")
        print()
        print(text)
        print("------------------------------------------------------------------------")
