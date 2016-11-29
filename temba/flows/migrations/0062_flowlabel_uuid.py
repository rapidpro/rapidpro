# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from temba.utils.models import generate_uuid


def populate_flowlabel_uuid(apps, schema_editor):
    FlowLabel = apps.get_model('flows', 'FlowLabel')
    for label in FlowLabel.objects.all():
        label.uuid = generate_uuid()
        label.save(update_fields=('uuid',))


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0061_exit_flowruns'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowlabel',
            name='uuid',
            field=models.CharField(null=True, max_length=36, help_text='The unique identifier for this label', unique=True, verbose_name='Unique Identifier', db_index=True),
        ),
        migrations.RunPython(populate_flowlabel_uuid),
        migrations.AlterField(
            model_name='flowlabel',
            name='uuid',
            field=models.CharField(default=generate_uuid, max_length=36,
                                   help_text='The unique identifier for this label', unique=True,
                                   verbose_name='Unique Identifier', db_index=True),
        ),
    ]
