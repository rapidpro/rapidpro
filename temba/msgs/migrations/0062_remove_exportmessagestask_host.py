# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0061_broadcast_recipients'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportmessagestask',
            name='host',
        ),
    ]
