# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count
from django_redis import get_redis_connection
from django.db.models import Q, Count
import time
import json
import math

def populate_redis_activity(apps, schema_editor):

    # Excuted migration actually used real model, to allow below
    # method call to do_calculate_flow_stats(). With the model
    # definition changing at 0023_new_split_dialog we can no longer
    # fetch this model. This redis data migration will be removed
    # when we squash for the first community release.

    # from temba.flows.models import Flow
    Flow = apps.get_model('flows', 'Flow')

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

    # print "Total time: %ss" % (time.time() - start)

class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0004_auto_20141215_1801'),
    ]

    operations = [
         migrations.RunPython(populate_redis_activity)
    ]
