# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0015_auto_20150703_2048'),
    ]

    operations = [
        migrations.CreateModel(
            name='ChannelCount',
            fields=[
                ('id', models.AutoField(verbose_name='ID', serialize=False, auto_created=True, primary_key=True)),
                ('count_type', models.CharField(help_text='What type of message this row is counting', max_length=2, choices=[('IM', 'Incoming Message'), ('OM', 'Outgoing Message'), ('IV', 'Incoming Voice'), ('OV', 'Outgoing Voice')])),
                ('day', models.DateField(null=True, help_text='The day this count is for')),
                ('count', models.IntegerField(default=0, help_text='The count of messages on this day')),
                ('channel', models.ForeignKey(help_text='The channel this is a daily summary count for', to='channels.Channel')),
            ],
            options={
            },
            bases=(models.Model,),
        ),
        migrations.AlterUniqueTogether(
            name='channelcount',
            unique_together=set([('channel', 'day', 'count_type')]),
        ),
    ]
