# -*- coding: utf-8 -*-
from __future__ import unicode_literals
import json

from django.db import models, migrations


def cast_json(apps, schema_editor):
    Org = apps.get_model("orgs", "Org")
    for org in Org.objects.all():
        webhook_json = {'url': org.webhook, 'method': 'POST'}
        org.webhook = json.dumps(webhook_json)
        org.save()

class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0004_auto_20150416_0728'),
    ]

    operations = [
        migrations.RunPython(cast_json),
    ]
