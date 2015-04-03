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
            field=models.CharField(help_text='The uuid of the next step type we took', max_length=36, null=True),
            preserve_default=True,
        ),
        migrations.AlterIndexTogether(
            name='flowstep',
            index_together=set([('step_uuid', 'next_uuid', 'rule_uuid', 'left_on')]),
        ),
        migrations.RunSQL(
            sql='create index flows_flowstep_step_next_left_null_rule on flows_flowstep(step_uuid, next_uuid,left_on) WHERE rule_uuid IS NULL;',
            reverse_sql='drop index flows_flowstep_step_next_left_null_rule;'
        ),
    ]
