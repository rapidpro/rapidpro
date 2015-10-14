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
        old_bucket_name = bucket_name.replace('-', '.')

        for msg in Msg.objects.filter(msg_type='I').exclude(recording_url=None):
            # if our recording URL is on our old bucket
            if msg.recording_url.find(old_bucket_name):
                # rename it to our new bucket
                msg.recording_url = msg.recording_url.replace(old_bucket_name, bucket_name)
                msg.save(update_fields=['recording_url'])

    operations = [
        migrations.RunPython(move_recording_domains)
    ]
