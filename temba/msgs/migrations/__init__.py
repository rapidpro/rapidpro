from django.db.models import Max

BATCH_SIZE = 5000
MSG_BATCH = BATCH_SIZE * 5


def update_msg_purge_status(Broadcast, Msg):

    from django.utils import timezone
    from datetime import timedelta

    # 90 days ago
    purge_date = timezone.now() - timedelta(days=90)

    if getattr(Msg, 'objects', None):
        msgs = Msg.objects
    else:
        msgs = Msg.current_messages

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
            max_msg_pk = msgs.aggregate(Max('pk'))['pk__max']
            for msg_offset in range(0, max_msg_pk+1, MSG_BATCH*5):
                # msg part of a purged broadcast
                msgs.filter(pk__gte=msg_offset,
                                   pk__lt=msg_offset+MSG_BATCH,
                                   purged__isnull=True,
                                   broadcast_id__in=purged_broadcasts).update(purged=True)

            # any other unset broadcasts are considered not purged
            Broadcast.objects.filter(pk__gte=offset,
                                     pk__lt=offset+BATCH_SIZE,
                                     purged__isnull=True).update(purged=False)

    max_pk = msgs.aggregate(Max('pk'))['pk__max']
    if max_pk is not None:
        print "Populating messages purged field.."
        for offset in range(0, max_pk+1, BATCH_SIZE):
            print 'On %d of %d' % (offset, max_pk)

            # all remaining messages
            msgs.filter(pk__gte=offset,
                               pk__lt=offset+BATCH_SIZE,
                               purged__isnull=True).update(purged=False)