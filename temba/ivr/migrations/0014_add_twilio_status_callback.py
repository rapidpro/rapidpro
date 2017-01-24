# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.core.urlresolvers import reverse
from django.conf import settings
from twilio.rest import TwilioRestClient
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
    twilio_orgs = Org.objects.filter(config__icontains='APPLICATION_SID')
    if twilio_orgs:
        print 'Updating %s orgs with twilio connections..' % len(twilio_orgs)

    for idx, twilio_org in enumerate(twilio_orgs):
        print '%d: Updating org %d' % ((idx + 1), twilio_org.pk)
        client = get_twilio_client(twilio_org)
        app_name = "%s/%d" % (settings.TEMBA_HOST.lower(), twilio_org.pk)
        app_url = "https://" + settings.TEMBA_HOST + "%s" % reverse('handlers.twilio_handler')
        apps = client.applications.list(friendly_name=app_name)
        if apps:
            for app in apps:
                app.update(voice_url=app_url, sms_url=app_url, status_callback=app_url, status_callback_method='POST')


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
