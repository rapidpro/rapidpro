# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations



def rebuild_flow_stats(apps, schema_editor):

    # kinda breaking migration rules here to not duplicate code, should be asking
    # for raw model from apps.get_model('flows', 'Flow'). This means this migration
    # can only run as long as the methods we are calling here exist.
    FlowRun = apps.get_model('flows', 'FlowRun')

    from django.utils import timezone
    from datetime import timedelta

    forty_five_days_ago = timezone.now() - timedelta(days=45)
    runs = FlowRun.objects.filter(expired_on__gte=forty_five_days_ago).exclude(flow__flow_type='M').distinct('flow')

    for run in runs:
        # request a longer lock during our migration
        print "Rebuilding flow stats for %s.." % run.flow
        run.flow.do_calculate_flow_stats(lock_ttl=600)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0006_remove_flowrun_uuid'),
    ]

    operations = [
        migrations.RunPython(rebuild_flow_stats)
    ]
