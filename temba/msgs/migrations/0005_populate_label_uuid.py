# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from uuid import uuid4


def populate_label_uuid(apps, schema_editor):
    Label = apps.get_model('msgs', 'Label')
    for label in Label.objects.all():
        label.uuid = unicode(uuid4())
        label.save()


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0004_label_uuid'),
    ]

    operations = [
        migrations.RunPython(populate_label_uuid)
    ]
