# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0039_derive_stopped'),
    ]

    def rename_groups_key(apps, schema_editor):
        ContactField = apps.get_model('contacts', 'ContactField')
        ContactField.objects.filter(key='groups').update(key='groups_field')

    operations = [
        migrations.RunPython(rename_groups_key)
    ]
