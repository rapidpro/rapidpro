# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0020_remove_exportflowresultstask_filename'),
    ]

    def populate_destination_uuids(apps, schema_editor):
        ActionSet = apps.get_model("flows", "ActionSet")
        # print "%d actionsets to update" % ActionSet.objects.all().count()

        for idx, actionset in enumerate(ActionSet.objects.all().select_related('destination')):
            if idx % 1000 == 0:
                print "Processed %d actionsets" % idx

            if actionset.destination:
                actionset.destination_uuid = actionset.destination.uuid
                actionset.destination_type = 'R'
                actionset.save(update_fields=['destination_uuid', 'destination_type'])

    operations = [

        # Add a new field for our destination uuid
        migrations.AddField(
            model_name='actionset',
            name='destination_uuid',
            field=models.CharField(max_length=36, null=True),
            preserve_default=True,
        ),

        # add in our destination type
        migrations.AddField(
            model_name='actionset',
            name='destination_type',
            field=models.CharField(max_length=1, null=True, choices=[('R', 'RuleSet'), ('A', 'ActionSet')]),
            preserve_default=True,
        ),

        # populate our new uuid field
        migrations.RunPython(populate_destination_uuids),

        # remove the old field
        migrations.RemoveField(
            model_name='actionset',
            name='destination',
        ),

        # now rename our new field to the same as the old one
        migrations.RenameField(
            model_name='actionset',
            old_name='destination_uuid',
            new_name='destination'
        ),
    ]
