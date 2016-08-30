# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('ivr', '0009_auto_20160414_0642'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ivrcall',
            name='contact',
            field=models.ForeignKey(related_name='calls', to='contacts.Contact', help_text='Who this call is with'),
        ),
    ]
