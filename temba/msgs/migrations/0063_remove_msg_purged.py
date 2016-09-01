# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0062_remove_exportmessagestask_host'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='msg',
            name='purged',
        ),
    ]
