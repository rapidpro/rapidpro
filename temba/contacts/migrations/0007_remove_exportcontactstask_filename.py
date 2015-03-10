# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0006_export_asset'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportcontactstask',
            name='filename',
        ),
    ]
