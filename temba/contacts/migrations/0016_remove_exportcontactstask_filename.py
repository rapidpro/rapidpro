# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0015_auto_20150423_0930'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='exportcontactstask',
            name='filename',
        ),
    ]
