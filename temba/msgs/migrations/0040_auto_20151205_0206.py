# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Max
from django.utils import timezone
from datetime import timedelta


def update_msg_purge_status(Broadcast, Msg, update_broadcasts=True, batch_size=5000, msg_start=0):

    msg_batch = batch_size * 5

    # 90 days ago
    purge_date = timezone.now() - timedelta(days=90)

    if getattr(Msg, 'objects', None):
        msgs = Msg.objects
    else:
        msgs = Msg.current_messages

    if update_broadcasts:
        max_pk = Broadcast.objects.aggregate(Max('pk'))['pk__max']
        if max_pk is not None:
            print "Populating broadcasts purged field.."
            for offset in range(0, max_pk+1, batch_size):
                print 'Broadcast %d of %d' % (offset, max_pk)

                # determine which broadcasts are old
                broadcasts = Broadcast.objects.filter(pk__gte=offset,
                                                      pk__lt=offset+batch_size,
                                                      created_on__lt=purge_date,
                                                      purged__isnull=True)

                # set our old broadcast purge
                broadcasts.update(purged=False)

                # store the broadcasts we purged
                purged_broadcasts = [b.id for b in broadcasts]

                # all the related messages for those broadcasts
                max_msg_pk = msgs.aggregate(Max('pk'))['pk__max']
                for msg_offset in range(msg_start, max_msg_pk+1, msg_batch):
                    # msg part of a purged broadcast
                    msgs.filter(pk__gte=msg_offset,
                                pk__lt=msg_offset+msg_batch,
                                purged__isnull=True,
                                broadcast_id__in=purged_broadcasts).update(purged=True)

                # any other unset broadcasts are considered not purged
                Broadcast.objects.filter(pk__gte=offset,
                pk__lt=offset+batch_size,
                                         purged__isnull=True).update(purged=False)

    max_pk = msgs.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating messages purged field.. (start %d)" % msg_start
        for offset in range(msg_start, max_pk+1, batch_size):
            print 'Msg %d of %d' % (offset, max_pk)

            # all remaining messages
            msgs.filter(pk__gte=offset,
                        pk__lt=offset+batch_size,
                        purged__isnull=True).update(purged=False)


def update_purge(apps, schema_editor):

    # we want to be non-atomic
    if schema_editor.connection.in_atomic_block:
            schema_editor.atomic.__exit__(None, None, None)
    update_msg_purge_status(apps.get_model('msgs', 'Broadcast'), apps.get_model('msgs', 'Msg'))


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0039_auto_20151204_2238'),
    ]

    operations = [
        migrations.RunPython(update_purge, atomic=False),
    ]
