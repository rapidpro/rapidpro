# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0046_auto_20160225_1809'),
    ]

    operations = [
        migrations.RenameField(
            model_name='msg',
            new_name='media',
            old_name='recording_url'
        )
    ]