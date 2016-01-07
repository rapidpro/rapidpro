# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0034_action_log_levels'),
    ]

    def populate_flowversion_version(apps, schema_editor):
        FlowVersion = apps.get_model('flows', 'FlowVersion')

        count = 1
        last_flow = None
        for version in FlowVersion.objects.all().order_by('flow__pk', 'created_on'):
            if version.flow.pk != last_flow:
                count = 1
            else:
                count += 1

            version.version = count
            version.save()
            last_flow = version.flow.pk

    operations = [
        migrations.RenameField(
            model_name='flowversion',
            old_name='version_number',
            new_name='spec_version',
        ),
        migrations.AlterField(
            model_name='actionlog',
            name='level',
            field=models.CharField(default='I', help_text='Log event level', max_length=1, choices=[('I', 'Info'), ('W', 'Warning'), ('E', 'Error')]),
            preserve_default=True,
        ),
        migrations.AlterField(
            model_name='flow',
            name='flow_type',
            field=models.CharField(default='F', help_text='The type of this flow', max_length=1, choices=[('F', 'Message flow'), ('M', 'Single Message Flow'), ('V', 'Phone call flow'), ('S', 'Android Survey')]),
            preserve_default=True,
        ),
        migrations.AddField(
            model_name='flowversion',
            name='version',
            field=models.IntegerField(help_text='Version counter for each definition', null=True),
            preserve_default=True,
        ),
        migrations.RunPython(populate_flowversion_version)

    ]
