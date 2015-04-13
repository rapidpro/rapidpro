# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    def populate_is_failed(apps, schema_editor):
        Contact = apps.get_model("contacts", "Contact")
        Contact.objects.filter(status='F').update(is_failed=True)

    dependencies = [
        ('contacts', '0009_auto_20150317_2235'),
    ]

    operations = [
        migrations.RunPython(
            populate_is_failed
        ),
    ]
