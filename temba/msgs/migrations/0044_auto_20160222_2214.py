# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0043_auto_20160222_2212'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='modified_on',
            field=models.DateTimeField(help_text='When this message was last modified', auto_now=True, null=True, verbose_name='Modified On', blank=True),
        ),
    ]
