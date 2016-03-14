# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0046_auto_20160225_1809'),
    ]

    operations = [
        migrations.AlterField(
            model_name='msg',
            name='response_to',
            field=models.ForeignKey(related_name='responses', verbose_name='Response To', blank=True, to='msgs.Msg', help_text='The message that this message is in reply to', null=True, db_index=False),
        ),
    ]
