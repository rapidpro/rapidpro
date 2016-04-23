# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('values', '0005_add_contact_field_value_idx'),
    ]

    operations = [
        migrations.RenameField(
            model_name='value',
            new_name='media_value',
            old_name='recording_value'
        ),
    ]
