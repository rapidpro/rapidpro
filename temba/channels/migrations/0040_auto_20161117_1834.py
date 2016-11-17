# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models
from django_redis import get_redis_connection


def migrate_active_queues(apps, schema_editor):
    r = get_redis_connection()
    queue_types = ['send_msg_task', 'handle_event_task', 'start_msg_flow_batch']
    for queue_type in queue_types:
        existing = r.keys('%s:*' % queue_type)

        # remove our current active set
        active_name = '%s:active' % queue_type
        r.delete(active_name)

        # and recreate our active set for each queue that has elements in it
        for queue in existing:
            if queue == active_name:
                continue

            org_id = int(queue[len(queue_type)+1:])
            r.zincrby(active_name, org_id, 0)


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0039_channellog_request_time'),
    ]

    operations = [
        migrations.RunPython(migrate_active_queues)
    ]
