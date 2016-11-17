# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0006_auto_20161010_1633'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trigger',
            name='channel',
            field=models.OneToOneField(null=True, to='channels.Channel', help_text='The associated channel', verbose_name='Channel'),
        ),
        migrations.AlterField(
            model_name='trigger',
            name='trigger_type',
            field=models.CharField(default='K', help_text='The type of this trigger', max_length=1, verbose_name='Trigger Type', choices=[('K', 'Keyword Trigger'), ('S', 'Schedule Trigger'), ('V', 'Inbound Call Trigger'), ('M', 'Missed Call Trigger'), ('C', 'Catch All Trigger'), ('F', 'Follow Account Trigger'), ('N', 'New Conversation Trigger')]),
        ),
        migrations.AlterUniqueTogether(
            name='trigger',
            unique_together=set([]),
        ),
    ]
