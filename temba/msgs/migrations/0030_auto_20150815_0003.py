# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0029_auto_20150803_2254'),
    ]

    operations = [
        migrations.AlterField(
            model_name='call',
            name='contact',
            field=models.ForeignKey(related_name='calls', verbose_name='Contact', to='contacts.Contact', help_text='The phone number for this call'),
            preserve_default=True,
        ),
    ]
