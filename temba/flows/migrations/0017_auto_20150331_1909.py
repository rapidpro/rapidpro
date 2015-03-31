# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0016_reorganize_exports'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowstep',
            name='next_uuid',
            field=models.CharField(help_text='The uuid of the next step type we took', max_length=36, null=True, db_index=True),
            preserve_default=True,
        ),
    ]
