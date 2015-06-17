# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0015_remove_label_parent'),
    ]

    operations = [
        migrations.AddField(
            model_name='label',
            name='visible_count',
            field=models.PositiveIntegerField(default=0, help_text='Number of non-archived messages with this label'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='label',
            name='folder',
            field=models.ForeignKey(related_name='children', verbose_name='Folder', to='msgs.Label', null=True),
            preserve_default=True,
        ),
    ]
