# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from django.db.models import Max, F

class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0044_auto_20160222_2214'),
    ]

    def update_batch(apps, batch):
        Msg = apps.get_model('msgs', 'Msg')

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
            Msg.current_messages.filter(id__in=sent_ids).update(modified_on=F('sent_on'))

        if queued_ids:
            Msg.current_messages.filter(id__in=queued_ids).update(modified_on=F('queued_on'))

        if created_ids:
            Msg.current_messages.filter(id__in=created_ids).update(modified_on=F('created_on'))

    def populate_modified_on(apps, schema):
        # outgoing messages without a modified_on date but which have a sent on, should have modified set to that
        Msg = apps.get_model('msgs', 'Msg')

        # get our max id
        max_id = Msg.all_messages.aggregate(Max('id'))['id__max']

        # 10,000 messages at a time in our range
        start_id = 0
        while start_id < max_id:
            # select our batch of msgs without a modified_on
            msgs = Msg.all_messages.filter(id__gt=start_id, id__lte=start_id+10000,
                                           modified_on=None).only('id', 'created_on', 'queued_on', 'sent_on')

            apps.update_batch(msgs)
            start_id += 10000

            print "%d of %d completed." % (start_id, max_id)

    operations = [
        migrations.RunPython(populate_modified_on)
    ]
