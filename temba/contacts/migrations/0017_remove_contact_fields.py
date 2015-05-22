# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0016_remove_exportcontactstask_filename'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='contact',
            name='fields',
        ),
    ]
