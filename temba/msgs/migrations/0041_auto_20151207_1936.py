# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0040_auto_20151205_0206'),
    ]

    operations = [
        migrations.AlterField(
            model_name='broadcast',
            name='purged',
            field=models.NullBooleanField(default=False, help_text='If the messages for this broadcast have been purged'),
        ),
        migrations.AlterField(
            model_name='msg',
            name='purged',
            field=models.NullBooleanField(default=False, help_text='If this message has been purged'),
        ),
    ]
