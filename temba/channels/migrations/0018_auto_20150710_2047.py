# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0017_install_channel_count_triggers'),
    ]

    operations = [
        # these triggers won't be used going forward
        migrations.RunSQL("DROP TRIGGER IF EXISTS "
                          "when_channellog_changes_then_update_channel_trg on channels_channellog;"
        ),
        migrations.RunSQL("DROP TRIGGER IF EXISTS "
                          "when_channellog_truncate_then_update_channel_trg on channels_channellog;"
        ),
        migrations.RemoveField(
            model_name='channel',
            name='error_log_count',
        ),
        migrations.RemoveField(
            model_name='channel',
            name='success_log_count',
        ),
        migrations.AlterField(
            model_name='channelcount',
            name='count',
            field=models.IntegerField(default=0, help_text='The count of messages on this day and type'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channelcount',
            name='count_type',
            field=models.CharField(help_text='What type of message this row is counting', max_length=2,
                                   choices=[('IM', 'Incoming Message'), ('OM', 'Outgoing Message'),
                                            ('IV', 'Incoming Voice'), ('OV', 'Outgoing Voice'),
                                            ('LS', 'Success Log Record'), ('LE', 'Error Log Record')]),
            preserve_default=True,
        ),
    ]
