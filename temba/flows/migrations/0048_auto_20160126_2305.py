# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0047_flowrun_indexes_2'),
    ]

    operations = [
        migrations.CreateModel(
            name='FlowRunCount',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('exit_type', models.CharField(max_length=1, null=True, choices=[('C', 'Completed'), ('I', 'Interrupted'), ('E', 'Expired')])),
                ('count', models.IntegerField(default=0)),
                ('flow', models.ForeignKey(related_name='counts', to='flows.Flow')),
            ],
        ),
        migrations.AlterIndexTogether(
            name='flowruncount',
            index_together=set([('flow', 'exit_type')]),
        ),
    ]
