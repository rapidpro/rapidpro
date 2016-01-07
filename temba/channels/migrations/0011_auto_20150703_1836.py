# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0010_auto_20150520_0929'),
    ]

    operations = [
        migrations.AddField(
            model_name='channel',
            name='error_log_count',
            field=models.IntegerField(default=0, verbose_name='The number of error messages in our ChannelLog'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='channel',
            name='success_log_count',
            field=models.IntegerField(default=0, verbose_name='The number of success messages in our ChannelLog'),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='channellog',
            name='channel',
            field=models.ForeignKey(to='channels.Channel', related_name='logs',
                                    help_text='The channel the message was sent on',
                                    null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='created_on',
            field=models.DateTimeField(help_text='When this log message was logged', auto_now_add=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='description',
            field=models.CharField(help_text='A description of the status of this message send', max_length=255),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='is_error',
            field=models.BooleanField(default=None, help_text='Whether an error was encountered when sending the message'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='method',
            field=models.CharField(help_text='The HTTP method used when sending the message', max_length=16, null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='msg',
            field=models.ForeignKey(help_text='The message that was sent', to='msgs.Msg'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='request',
            field=models.TextField(help_text='The body of the request used when sending the message', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='response',
            field=models.TextField(help_text='The body of the response received when sending the message', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='response_status',
            field=models.IntegerField(help_text='The response code received when sending the message', null=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='channellog',
            name='url',
            field=models.TextField(help_text='The URL used when sending the message', null=True),
            preserve_default=True,
        ),
    ]
