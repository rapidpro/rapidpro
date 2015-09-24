# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0005_auto_20150416_0729'),
        ('flows', '0026_auto_20150805_0504'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='modified_on',
            field=models.DateTimeField(help_text='When this flow run was last updated', auto_now=True, null=True),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowrun',
            name='org',
            field=models.ForeignKey(related_name='runs', null=True, to='orgs.Org', db_index=False),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='expired_on',
            field=models.DateTimeField(help_text='When this flow run expired', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='expires_on',
            field=models.DateTimeField(help_text='When this flow run will expire', null=True),
            preserve_default=True,
        ),
    ]
