# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from functools import wraps

from django.db import migrations, models
from django.db.models import Max

BATCH_SIZE = 5000
MSG_BATCH = BATCH_SIZE * 5

def non_atomic_migration(func):
    @wraps(func)
    def wrapper(apps, schema_editor):
        if schema_editor.connection.in_atomic_block:
            schema_editor.atomic.__exit__(None, None, None)
        return func(apps, schema_editor)
    return wrapper


@non_atomic_migration
def initialize_data(apps, schema_editor):

    Broadcast = apps.get_model("msgs", "Broadcast")
    Msg = apps.get_model("msgs", "Msg")

    from django.utils import timezone
    from datetime import timedelta

    # 90 days ago
    purge_date = timezone.now() - timedelta(days=90)

    max_pk = Broadcast.objects.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating broadcasts purged field.."
        for offset in range(0, max_pk+1, BATCH_SIZE):
            print 'On %d of %d' % (offset, max_pk)

            # determine which broadcasts are old
            broadcasts = Broadcast.objects.filter(pk__gte=offset,
                                                  pk__lt=offset+BATCH_SIZE,
                                                  created_on__lt=purge_date,
                                                  purged__isnull=True)

            # set our old broadcast purge
            broadcasts.update(purged=False)

            # store the broadcasts we purged
            purged_broadcasts = [b.id for b in broadcasts]

            # all the related messages for those broadcasts
            max_msg_pk = Msg.objects.aggregate(Max('pk'))['pk__max']
            for msg_offset in range(0, max_msg_pk+1, MSG_BATCH*5):
                # msg part of a purged broadcast
                Msg.objects.filter(pk__gte=msg_offset,
                                   pk__lt=msg_offset+MSG_BATCH,
                                   purged__isnull=True,
                                   broadcast_id__in=purged_broadcasts).update(purged=True)

            # any other unset broadcasts are considered not purged
            Broadcast.objects.filter(pk__gte=offset,
                                     pk__lt=offset+BATCH_SIZE,
                                     purged__isnull=True).update(purged=False)

    max_pk = Msg.objects.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating messages purged field.."
        for offset in range(0, max_pk+1, BATCH_SIZE):
            print 'On %d of %d' % (offset, max_pk)

            # all remaining messages
            Msg.objects.filter(pk__gte=offset,
                               pk__lt=offset+BATCH_SIZE,
                               purged__isnull=True).update(purged=False)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0039_auto_20151204_2238'),
    ]

    operations = [
        migrations.RunPython(initialize_data, atomic=False),
    ]
