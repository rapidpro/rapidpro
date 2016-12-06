from __future__ import absolute_import, unicode_literals

import re

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.validators import EmailValidator
from django.template import loader, Context
from django.conf import settings


class TembaEmailValidator(EmailValidator):
    user_regex = re.compile(
        r"(^[-!#$%&'*+/=?^_`{}|~0-9A-Z]+(\.[-!#$%&'*+/=?^_`{}|~0-9A-Z]+)*\Z"  # dot-atom
        r'|^"([\001-\010\013\014\016-\[\]-\177]|\\[\001-\011\013\014\016-\177])*"\Z)',  # quoted-string
        re.IGNORECASE)
    domain_regex = re.compile(
        # max length for domain name labels is 63 characters per RFC 1034
        r'(?:[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?\.)+(?:[A-Z]{2,63}|[A-Z0-9-]{2,}(?<!-))\Z',
        re.IGNORECASE)


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


def link_components(request=None, user=None):
    """
    Context provider for email templates
    """
    protocol = 'https' if request.is_secure() else 'http'
    hostname = request.branding['domain']
    return {'protocol': protocol, 'hostname': hostname}


def send_simple_email(recipients, subject, body):
    """
    Sends a simple text email to the given recipients

    :param recipients: address or list of addresses to send the mail to
    :param subject: subject of the email
    :param body: body of the email
    """
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'website@rapidpro.io')
    recipient_list = [recipients] if isinstance(recipients, basestring) else recipients

    send_temba_email(subject, body, None, from_email, recipient_list)


def send_template_email(recipients, subject, template, context, branding):
    """
    Sends a multi-part email rendered from templates for the text and html parts

    :param recipients: address or list of addresses to send the mail to
    :param subject: subject of the email
    :param template: name of the template, without .html or .txt ('channels/email/power_charging')
    :param context: dictionary of context variables
    :param branding: branding of the host
    """
    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'website@rapidpro.io')
    recipient_list = [recipients] if isinstance(recipients, basestring) else recipients

    html_template = loader.get_template(template + ".html")
    text_template = loader.get_template(template + ".txt")

    context['subject'] = subject
    context['branding'] = branding

    html = html_template.render(Context(context))
    text = text_template.render(Context(context))

    send_temba_email(subject, text, html, from_email, recipient_list)


def send_temba_email(subject, text, html, from_email, recipient_list):
    """
    Actually sends the email. Having this as separate function makes testing multi-part emails easier
    """
    if settings.SEND_EMAILS:
        if html is not None:
            message = EmailMultiAlternatives(subject, text, from_email, recipient_list)
            message.attach_alternative(html, "text/html")
            message.send()
        else:
            send_mail(subject, text, from_email, recipient_list)
    else:
        # just print to console if we aren't meant to send emails
        print "----------- Skipping sending email, SEND_EMAILS to set False -----------"
        print text
        print "------------------------------------------------------------------------"
