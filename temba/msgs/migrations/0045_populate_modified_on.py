# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Max, F
import locale


def update_batch(manager, batch):
    # iterate through, keep track of those that have sent_on, queued_on or only created_on (in that priority)
    sent_ids = list()
    queued_ids = list()
    created_ids = list()

    for msg in batch:
        if msg.sent_on:
            sent_ids.append(msg.id)
        elif msg.queued_on:
            queued_ids.append(msg.id)
        else:
            created_ids.append(msg.id)

    # now update modified on appropriate for each group
    if sent_ids:
        manager.filter(id__in=sent_ids).update(modified_on=F('sent_on'))

    if queued_ids:
        manager.filter(id__in=queued_ids).update(modified_on=F('queued_on'))

    if created_ids:
        manager.filter(id__in=created_ids).update(modified_on=F('created_on'))


def populate_modified_on(manager):
    # get our max id
    max_id = manager.aggregate(Max('id'))['id__max']

    # 10,000 messages at a time in our range
    # The belief is that this is a faster way of updating the ~190M messages currently in RapidPro
    # as it essentially forces Postgres to only look at 10k messages in turn (using the sequential id index)
    # Doing any kind of query on modified_on is a non starter since it isn't indexed (and we don't want it to be on
    # all messages).
    start_id = 0
    while start_id < max_id:
        # select our batch of msgs without a modified_on
        msgs = manager.filter(id__gt=start_id, id__lte=start_id+10000,
                              modified_on=None).only('id', 'created_on', 'queued_on', 'sent_on')

        update_batch(manager, msgs)
        start_id += 10000

        print "%s of %s completed." % (locale.format("%d", start_id, grouping=True),
                                       locale.format("%d", max_id, grouping=True))


def run_migration(apps, schema):
    MsgModel = apps.get_model('msgs', 'Msg')
    populate_modified_on(MsgModel.objects)


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0044_auto_20160222_2214'),
    ]

    operations = [
        migrations.RunPython(run_migration)
    ]
