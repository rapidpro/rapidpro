# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0038_broadcast_purged'),
    ]

    operations = [
        migrations.AddField(
            model_name='msg',
            name='purged',
            field=models.NullBooleanField(help_text='If this message has been purged'),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='broadcast',
            name='purged',
            field=models.NullBooleanField(help_text='If the messages for this broadcast have been purged'),
            preserve_default=True
        ),
        migrations.RunSQL(
            'ALTER TABLE msgs_msg ALTER COLUMN purged SET DEFAULT false',
            'ALTER TABLE msgs_msg ALTER COLUMN purged DROP DEFAULT',
            state_operations=[
                migrations.AlterField(
                    model_name='msg',
                    name='purged',
                    field=models.NullBooleanField(default=False),
                    preserve_default=True
                )
            ],
        ),
        migrations.RunSQL(
            'ALTER TABLE msgs_broadcast ALTER COLUMN purged SET DEFAULT false',
            'ALTER TABLE msgs_broadcast ALTER COLUMN purged DROP DEFAULT',
            state_operations=[
                migrations.AlterField(
                    model_name='broadcast',
                    name='purged',
                    field=models.NullBooleanField(default=False),
                    preserve_default=True
                )
            ],
        )
    ]
