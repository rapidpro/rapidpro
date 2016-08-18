# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0041_indexes_update'),
        ('msgs', '0059_indexes_update'),
    ]

    operations = [
        migrations.AddField(
            model_name='broadcast',
            name='recipient_contacts',
            field=models.ManyToManyField(help_text='The contacts which received this message', related_name='broadcasts', verbose_name='Recipients', to='contacts.Contact'),
        ),
    ]
