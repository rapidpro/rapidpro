# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


def populate_exit_type_for_expired(apps, schema_editor):
    FlowRun = apps.get_model('flows', 'FlowRun')

    # before expired_on gets renamed to exited_on - need to record that these runs are expired
    num_expired = FlowRun.objects.exclude(expired_on=None).update(exit_type='E')
    if num_expired:
        print "Set exit type for %d expired runs" % num_expired


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
        migrations.RunPython(populate_exit_type_for_expired),
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
