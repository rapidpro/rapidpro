# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0017_auto_20150331_1909'),
    ]

    operations = [
        migrations.AlterField(
            model_name='flowstep',
            name='left_on',
            field=models.DateTimeField(help_text='When the user left this step in the flow', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flowstep',
            name='next_uuid',
            field=models.CharField(help_text='The uuid of the next step type we took', max_length=36, null=True),
            preserve_default=True,
        ),
        migrations.AlterIndexTogether(
            name='flowstep',
            index_together=set([('step_uuid', 'next_uuid', 'rule_uuid', 'left_on')]),
        ),
    ]
