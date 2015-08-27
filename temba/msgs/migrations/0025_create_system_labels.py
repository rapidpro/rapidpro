# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations


def create_system_labels(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')
    SystemLabel = apps.get_model('msgs', 'SystemLabel')

    for org in Org.objects.all():
        for label_type in ('I', 'W', 'A', 'O', 'S', 'X', 'E', 'C'):
            SystemLabel.objects.create(org=org, label_type=label_type)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0024_system_labels'),
    ]

    operations = [
        migrations.RunPython(create_system_labels)
    ]
