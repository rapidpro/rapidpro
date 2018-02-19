# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

import re
import six

from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, send_mail, get_connection as get_smtp_connection
from django.core.validators import EmailValidator
from django.template import loader
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


def send_simple_email(recipients, subject, body, from_email=None):
    """
    Sends a simple text email to the given recipients

    :param recipients: address or list of addresses to send the mail to
    :param subject: subject of the email
    :param body: body of the email
    """
    if from_email is None:
        from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'website@rapidpro.io')

    recipient_list = [recipients] if isinstance(recipients, six.string_types) else recipients

    send_temba_email(subject, body, None, from_email, recipient_list)


def send_custom_smtp_email(recipients, subject, body, from_email, smtp_host, smtp_port, smtp_username, smtp_password, use_tls):
    """
    Sends a text email to the given recipients using the SMTP configuration

    :param recipients: address or list of addresses to send the mail to
    :param subject: subject of the email
    :param body: body of the email
    :param from_email: the email address we wills end from
    :param smtp_host: SMTP server
    :param smtp_port: SMTP port
    :param smtp_username: SMTP username
    :param smtp_password: SMTP password
    :param use_tls: Whether to use TLS
    """
    recipient_list = [recipients] if isinstance(recipients, six.string_types) else recipients

    if smtp_port is not None:
        smtp_port = int(smtp_port)

    connection = get_smtp_connection(None, fail_silently=False, host=smtp_host, port=smtp_port, username=smtp_username,
                                     password=smtp_password, use_tls=use_tls)

    send_temba_email(subject, body, None, from_email, recipient_list, connection=connection)


def send_template_email(recipients, subject, template, context, branding):
    """
    Sends a multi-part email rendered from templates for the text and html parts

    :param recipients: address or list of addresses to send the mail to
    :param subject: subject of the email
    :param template: name of the template, without .html or .txt ('channels/email/power_charging')
    :param context: dictionary of context variables
    :param branding: branding of the host
    """

    # brands are allowed to give us a from address
    from_email = branding.get('from_email', getattr(settings, 'DEFAULT_FROM_EMAIL', 'website@rapidpro.io'))
    recipient_list = [recipients] if isinstance(recipients, six.string_types) else recipients

    html_template = loader.get_template(template + ".html")
    text_template = loader.get_template(template + ".txt")

    context['subject'] = subject
    context['branding'] = branding

    html = html_template.render(context)
    text = text_template.render(context)

    send_temba_email(subject, text, html, from_email, recipient_list)


def send_temba_email(subject, text, html, from_email, recipient_list, connection=None):
    """
    Actually sends the email. Having this as separate function makes testing multi-part emails easier
    """
    if settings.SEND_EMAILS:
        if html is not None:
            message = EmailMultiAlternatives(subject, text, from_email, recipient_list, connection=connection)
            message.attach_alternative(html, "text/html")
            message.send()
        else:
            send_mail(subject, text, from_email, recipient_list, connection=connection)
    else:
        # just print to console if we aren't meant to send emails
        print("----------- Skipping sending email, SEND_EMAILS to set False -----------")
        print(text)
        print("------------------------------------------------------------------------")
