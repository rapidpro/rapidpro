# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0019_unblock_unfail_test_contacts'),
    ]

    operations = [
        migrations.AddField(
            model_name='exportcontactstask',
            name='is_finished',
            field=models.BooleanField(default=False, help_text='Whether this export has completed'),
            preserve_default=True,
        ),
        migrations.RunSQL("UPDATE contacts_exportcontactstask SET is_finished=TRUE;"),
    ]
