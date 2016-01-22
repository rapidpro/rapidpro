# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from redis_cache import get_redis_connection
from temba.flows.models import FlowRun


def remove_expired_flows_from_active(apps, schema_editor):
    r = get_redis_connection()
    for key in r.keys('*:step_active_set:*'):
        # make sure our flow run activity is removed
        runs = FlowRun.objects.filter(pk__in=r.smembers(key), is_active=False, contact__is_test=False)
        FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_EXPIRED)


class Migration(migrations.Migration):

    dependencies = [
        ('flows', '0012_auto_20150302_1734'),
    ]

    operations = [
        migrations.RunPython(remove_expired_flows_from_active)
    ]
