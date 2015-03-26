# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


def delete_old_poll_labels(apps, schema_editor):
    Label = apps.get_model('msgs', 'Label')
    Label.objects.exclude(label_type='M').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0006_auto_20150312_0827'),
    ]

    operations = [
        migrations.RunPython(delete_old_poll_labels)
    ]
