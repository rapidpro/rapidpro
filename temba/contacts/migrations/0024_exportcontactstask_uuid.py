# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0023_remove_test_contacts_from_sys_groups'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportcontactstask',
            name='uuid',
            field=models.CharField(help_text='The uuid used to name the resulting export file', max_length=36, null=True),
            preserve_default=True,
        ),
    ]
