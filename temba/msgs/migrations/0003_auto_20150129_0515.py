# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0002_broadcast_channel'),
    ]

    operations = [
        migrations.AddField(
            model_name='msg',
            name='recording_url',
            field=models.URLField(help_text='The url for any recording associated with this message', max_length=255, null=True, blank=True),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='msg',
            name='msg_type',
            field=models.CharField(default='I', help_text='The type of this message', max_length=1, verbose_name='Message Type', choices=[('I', 'Inbox Message'), ('F', 'Flow Message'), ('V', 'IVR Message')]),
            preserve_default=True,
        ),
    ]
