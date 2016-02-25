# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0042_update_triggers'),
    ]

    operations = [
        migrations.RenameField(
            model_name='msg',
            old_name='delivered_on',
            new_name='modified_on',
        ),
    ]
