# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0022_exportflowresultstask_is_finished'),
    ]

    operations = [

        # Add a nullable flow version field
        migrations.AddField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(help_text='The flow version this definition is in', null=True),
            preserve_default=True,
        ),

        migrations.AddField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(help_text='The flow version this definition is in', null=True),
            preserve_default=True,
        ),

        # assume all previous versions as version 4
        migrations.RunSQL(
            sql='update flows_flowversion set version_number=4',
            reverse_sql='update flows_flowversion set version_number=null',
        ),

        # assume all current versions as version 4
        migrations.RunSQL(
            sql='update flows_flow set version_number=4',
            reverse_sql='update flows_flow set version_number=null',
        ),

        # set our flow version to be required
        migrations.AlterField(
            model_name='flowversion',
            name='version_number',
            field=models.IntegerField(help_text='The flow version this definition is in'),
            preserve_default=True,
        ),

        migrations.AlterField(
            model_name='flow',
            name='version_number',
            field=models.IntegerField(help_text='The flow version this definition is in'),
            preserve_default=True,
        ),

        # add a nullable ruleset_type field
        migrations.AddField(
            model_name='ruleset',
            name='ruleset_type',
            field=models.CharField(help_text='The type of ruleset', max_length=16, null=True, choices=[('wait_message', 'Wait for message'), ('wait_recording', 'Wait for recording'), ('wait_digit', 'Wait for digit'), ('wait_digits', 'Wait for digits'), ('webhook', 'Webhook'), ('flow_field', 'Split on flow field'), ('contact_field', 'Split on contact field'), ('expression', 'Split by expression')]),
            preserve_default=True,
        )
    ]
