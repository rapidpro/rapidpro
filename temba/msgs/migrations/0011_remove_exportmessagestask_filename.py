# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0010_reorganize_exports'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportmessagestask',
            name='filename',
        ),
    ]
