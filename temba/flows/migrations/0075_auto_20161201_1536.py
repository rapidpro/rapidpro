# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.sql import InstallSQL

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0074_auto_20161116_2213'),
    ]

    operations = [
        migrations.CreateModel(
            name='FlowPathCount',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('from_uuid', models.UUIDField(help_text='Which flow node they came from')),
                ('to_uuid', models.UUIDField(help_text='Which flow node they went to')),
                ('period', models.DateTimeField(help_text='When the activity occured with hourly precision')),
                ('count', models.IntegerField(default=0)),
                ('flow', models.ForeignKey(related_name='activity', to='flows.Flow', help_text='The flow where the activity occurred')),
            ],
        ),
        migrations.AlterIndexTogether(
            name='flowpathcount',
            index_together=set([('flow', 'from_uuid', 'to_uuid', 'period')]),
        ),
        InstallSQL('0075_flows')
    ]
