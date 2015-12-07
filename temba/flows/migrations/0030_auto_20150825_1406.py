# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0029_populate_run_modified_on'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowrun',
            name='modified_on',
            field=models.DateTimeField(help_text='When this flow run was last updated', auto_now=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='org',
            field=models.ForeignKey(related_name='runs', to='orgs.Org', db_index=False),
            preserve_default=True,
        ),
    ]
