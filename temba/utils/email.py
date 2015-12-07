from __future__ import absolute_import, unicode_literals

import regex

from django.core.mail import EmailMultiAlternatives, send_mail
from django.template import loader, Context
from django.conf import settings


ADDRESS_REGEX = regex.compile(r'\S+@\S+\.\S+')


def is_valid_address(address):
    """
    Very loose email address check
    """
    return address and ADDRESS_REGEX.match(address)


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
