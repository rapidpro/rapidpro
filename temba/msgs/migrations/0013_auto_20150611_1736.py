# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0012_create_indexes'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='org',
            field=models.ForeignKey(related_name='msgs', verbose_name='Org', to='orgs.Org', help_text='The org this message is connected to', db_index=False),
            preserve_default=True,
        ),
    ]
