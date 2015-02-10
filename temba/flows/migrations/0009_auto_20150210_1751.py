# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations

def populate_export_flow_results_task_org(apps, schema_editor):
    model = apps.get_model("flows", "ExportFlowResultsTask")

    # delete first export task that has no flows
    model.objects.filter(flows=None).delete()

    # add org for all export task missing an org
    for obj in model.objects.filter(org=None):
        org = obj.flows.first().org
        obj.org = org
        obj.save(update_fields=('org',))

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0008_exportflowresultstask_org'),
    ]

    operations = [
        migrations.RunPython(populate_export_flow_results_task_org)
    ]
