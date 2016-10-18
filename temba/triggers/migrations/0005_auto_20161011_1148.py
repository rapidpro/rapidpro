# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('triggers', '0004_auto_20161010_1633'),
    ]

    operations = [
        migrations.AlterField(
            model_name='trigger',
            name='channel',
            field=models.ForeignKey(verbose_name='Channel', to='channels.Channel', help_text='The associated channel', null=True),
        ),
        migrations.AlterUniqueTogether(
            name='trigger',
            unique_together=set([('keyword', 'channel')]),
        ),
    ]
