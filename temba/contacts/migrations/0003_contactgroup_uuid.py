# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from uuid import uuid4


def populate_group_uuid(apps, schema_editor):
    ContactGroup = apps.get_model("contacts", "ContactGroup")
    for group in ContactGroup.objects.all():
        group.uuid = uuid4()
        group.save(update_fields=('uuid',))


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0002_auto_20141126_2054'),
    ]

    operations = [
        migrations.AddField(
            model_name='contactgroup',
            name='uuid',
            field=models.CharField(help_text='The unique identifier for this contact.', max_length=36, unique=True, null=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
        migrations.RunPython(
            populate_group_uuid
        ),
        migrations.AlterField(
            model_name='contactgroup',
            name='uuid',
            field=models.CharField(help_text='The unique identifier for this contact.', max_length=36, unique=True, verbose_name='Unique Identifier'),
            preserve_default=True,
        ),
    ]
