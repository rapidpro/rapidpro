# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0027_auto_20150820_2030'),
    ]

    def populate_flowrun_orgs(apps, schema_editor):
        Org = apps.get_model('orgs', 'Org')
        FlowRun = apps.get_model('flows', 'FlowRun')

        for org in Org.objects.all():
            FlowRun.objects.filter(flow__org=org).update(org_id=org.id)

    operations = [
        migrations.RunPython(populate_flowrun_orgs),
    ]
