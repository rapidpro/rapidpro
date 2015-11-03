# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0037_auto_20151023_1704'),
    ]

    operations = [
        migrations.RenameField(
            model_name='flowversion',
            old_name='version',
            new_name='revision'
        ),
        migrations.AlterField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(default=8, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowversion',
            name='flow',
            field=models.ForeignKey(related_name='revision', to='flows.Flow'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowversion',
            name='spec_version',
            field=models.IntegerField(default=8, help_text='The flow version this definition is in'),
            preserve_default=True,
        ),
    ]
