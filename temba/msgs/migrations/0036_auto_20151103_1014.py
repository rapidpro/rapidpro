# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0035_broadcast_expressions'),
    ]

    operations = [
        migrations.AlterField(
            model_name='exportmessagestask',
            name='groups',
            field=models.ManyToManyField(to='contacts.ContactGroup'),
        ),
    ]
