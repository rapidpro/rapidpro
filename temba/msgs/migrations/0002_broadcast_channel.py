# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0003_auto_20141128_2132'),
        ('msgs', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='channel',
            field=models.ForeignKey(verbose_name='Channel', to='channels.Channel', help_text='Channel to use for message sending', null=True),
            preserve_default=True,
        ),
    ]
