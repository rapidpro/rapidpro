# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count
from redis_cache import get_redis_connection
from django.db.models import Q, Count
import time
import json
import math

def populate_redis_activity(apps, schema_editor):

    # kinda breaking migration rules here to not duplicate code, should be asking
    # for raw model from apps.get_model('flows', 'Flow'). This means this migration
    # can only run as long as the methods we are calling here exist.
    from temba.flows.models import Flow

    print
    start = time.time()
    flows = Flow.objects.all().order_by('pk')
    total = flows.count()
    last_pct = 0

    for idx, flow in enumerate(flows):
        flow_time = time.time()
        flow.do_calculate_flow_stats()
        pct = math.floor(float(idx) / float(total)*100)
        if pct != last_pct:
            print "%d%% flows built" % pct
        last_pct = pct

    print "Total time: %ss" % (time.time() - start)

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0004_auto_20141215_1801'),
    ]

    operations = [
         migrations.RunPython(populate_redis_activity)
    ]
