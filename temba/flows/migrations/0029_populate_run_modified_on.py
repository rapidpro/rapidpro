# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import F

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0028_populate_flowrun_orgs'),
    ]

    def populate_flowrun_modified_on(apps, schema_editor):
        FlowRun = apps.get_model('flows', 'FlowRun')

        # update all flow runs that have already expired, their modified on is their expired on
        FlowRun.objects.exclude(expired_on=None).update(modified_on=F('expired_on'))

        # the rest will be determined based on their last step
        updated = 0
        for run in FlowRun.objects.filter(modified_on=None):
            latest_step = run.steps.order_by('-arrived_on').first()
            if latest_step:
                run.modified_on = latest_step.arrived_on
            else:
                run.modified_on = run.created_on
            run.save()

            updated += 1
            if updated % 1000:
                print "  Updated %d runs" % updated

    operations = [
        migrations.RunPython(populate_flowrun_modified_on),
    ]
