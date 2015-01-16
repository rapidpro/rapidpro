from email.mime.image import MIMEImage
from django.core.mail import EmailMultiAlternatives
from django.template import loader, Context
from django.conf import settings


def send_temba_email(to_email, subject, template, context, branding):
    """
    Utility method that sends a pretty email, attaching our logo
    to the header.

    :param to_email: The email address to send the mail to
    :param subject: The subject of the mail
    :param template: The name of the template, without .html or .txt ('channels/email/power_charging')
    :param context: The dictionary of context variables
    :param branding: The branding of the host
    """
    # skip if we aren't meant to send emails
    if not settings.SEND_EMAILS:
        print "!! Skipping sending email, SEND_EMAILS to set False"
        return

    from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'website@rapidpro.io')

    html_template = loader.get_template(template + ".html")
    text_template = loader.get_template(template + ".txt")

    context['subject'] = subject
    context['branding'] = branding

    html = html_template.render(Context(context))
    text = text_template.render(Context(context))

    message = EmailMultiAlternatives(subject, text, from_email, [to_email])
    message.attach_alternative(html, "text/html")
    message.send()
