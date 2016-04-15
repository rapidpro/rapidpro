# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0050_auto_20160414_0642'),
    ]

    operations = [
        migrations.RenameField(
            model_name='msg',
            new_name='media',
            old_name='recording_url'
        )
    ]