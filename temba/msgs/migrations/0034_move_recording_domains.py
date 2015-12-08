# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.conf import settings

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0033_exportmessagestask_uuid'),
    ]

    def move_recording_domains(apps, schema_editor):
        Msg = apps.get_model('msgs', 'Msg')

        # this is our new bucket name
        bucket_name = settings.AWS_STORAGE_BUCKET_NAME

        # our old bucket name had periods instead of dashes
        old_bucket_domain = 'http://' + bucket_name.replace('-', '.')

        # our new domain is more specific
        new_bucket_domain = 'https://' + settings.AWS_BUCKET_DOMAIN

        for msg in Msg.objects.filter(direction='I', msg_type='V').exclude(recording_url=None):
            # if our recording URL is on our old bucket
            if msg.recording_url.find(old_bucket_domain) >= 0:
                # rename it to our new bucket
                old_recording_url = msg.recording_url
                msg.recording_url = msg.recording_url.replace(old_bucket_domain,
                                                              new_bucket_domain)
                print "[%d] %s to %s" % (msg.id, old_recording_url, msg.recording_url)
                msg.save(update_fields=['recording_url'])

    operations = [
        migrations.RunPython(move_recording_domains)
    ]
