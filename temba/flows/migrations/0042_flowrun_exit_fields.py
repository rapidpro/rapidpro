# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0041_flowrun_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='flowrun',
            name='exit_type',
            field=models.CharField(help_text='Why the contact exited this flow run', max_length=1, null=True, choices=[('C', 'Completed'), ('S', 'Stopped'), ('E', 'Expired')]),
        ),
        migrations.RenameField(
            model_name='flowrun',
            old_name='expired_on',
            new_name='exited_on',
        ),
        migrations.AlterField(
            model_name='flowrun',
            name='exited_on',
            field=models.DateTimeField(help_text='When the contact exited this flow run', null=True),
        )
    ]
