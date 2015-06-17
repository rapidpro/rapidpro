# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0014_labels_to_folders'),
    ]

    operations = [
        migrations.AlterUniqueTogether(
            name='label',
            unique_together=set([('org', 'name')]),
        ),
        migrations.RemoveField(
            model_name='label',
            name='parent',
        ),
    ]
