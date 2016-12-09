# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0043_auto_20161111_1850'),
    ]

    operations = [
        migrations.AlterField(
            model_name='contacturn',
            name='urn',
            field=models.CharField(help_text='The Universal Resource Name as a string. ex: tel:+250788383383', max_length=255, choices=[('tel', 'Phone number'), ('facebook', 'Facebook identifier'), ('twitter', 'Twitter handle'), ('viber', 'Viber identifier'), ('line', 'LINE identifier'), ('telegram', 'Telegram identifier'), ('mailto', 'Email address'), ('ext', 'External identifier')]),
        ),
    ]
