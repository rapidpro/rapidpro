# -*- coding: utf-8 -*-
from __future__ import absolute_import, division, print_function, unicode_literals

from django.db import migrations
from django.core.urlresolvers import reverse
from django.conf import settings
from twilio.rest import TwilioRestClient, TwilioException
import json


def get_twilio_client(org):
    config = org.config
    if config:
        config = json.loads(config)
        account_sid = config.get('ACCOUNT_SID', None)
        auth_token = config.get('ACCOUNT_TOKEN', None)
        if account_sid and auth_token:
            return TwilioRestClient(account_sid, auth_token)
    return None


def fix_twilio_twiml_apps(Org):

    # only run this for production environments
    if settings.IS_PROD:
        twilio_orgs = Org.objects.filter(config__icontains='APPLICATION_SID').order_by('created_on')
        if twilio_orgs:
            print('Updating %s orgs with twilio connections..' % len(twilio_orgs))

        for idx, twilio_org in enumerate(twilio_orgs):
            print('%d: Updating %s (%d)' % ((idx + 1), twilio_org.name, twilio_org.id))
            client = get_twilio_client(twilio_org)

            if client:
                app_name = "%s/%d" % (settings.HOSTNAME.lower(), twilio_org.pk)
                app_url = "https://" + settings.HOSTNAME + "%s" % reverse('handlers.twilio_handler')

                try:
                    apps = client.applications.list(friendly_name=app_name)

                    if apps:
                        for app in apps:
                            print('     Updating %s (Last: %s, Voice: %s Callback: %s)' % (app.friendly_name, app.date_updated, app.voice_url, app.status_callback))
                            app.update(voice_url=app_url, sms_url=app_url, status_callback=app_url, status_callback_method='POST')
                    else:
                        print('     No apps found for %s' % twilio_org.name)
                except TwilioException:
                    print('     Connection failed for %s, skipping..' % twilio_org.name)
            else:
                print("     Couldn't get TwilioClient for %s" % twilio_org.name)


def apply_as_migration(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    fix_twilio_twiml_apps(Org)


def apply_manual():
    from temba.orgs.models import Org
    fix_twilio_twiml_apps(Org)


class Migration(migrations.Migration):
    dependencies = [
        ('ivr', '0013_reset_1'),
    ]

    operations = [
        migrations.RunPython(apply_as_migration)
    ]
