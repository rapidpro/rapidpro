# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0012_create_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='label',
            name='folder',
            field=models.ForeignKey(related_name='labels', verbose_name='Folder', to='msgs.Label', null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='label',
            name='label_type',
            field=models.CharField(default='L', help_text='Label type', max_length=1, choices=[('F', 'User Defined Folder'), ('L', 'User Defined Label')]),
            preserve_default=True,
        ),
    ]
