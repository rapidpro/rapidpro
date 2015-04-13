# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0008_auto_20150317_2228'),
    ]

    operations = [
        migrations.AddField(
            model_name='contact',
            name='is_failed',
            field=models.BooleanField(default=False, help_text='Whether we cannot send messages to this contact', verbose_name='Is Failed'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='contact',
            name='is_blocked',
            field=models.BooleanField(default=False, help_text='Whether this contact has been blocked', verbose_name='Is Blocked'),
            preserve_default=True,
        ),
    ]
