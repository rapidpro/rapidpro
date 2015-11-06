# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0026_channel_scheme'),
    ]

    operations = [
        migrations.AlterField(
            model_name='channel',
            name='alert_email',
            field=models.EmailField(help_text='We will send email alerts to this address if experiencing issues sending', max_length=254, null=True, verbose_name='Alert Email', blank=True),
        ),
    ]
