# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('msgs', '0036_auto_20151103_1014'),
    ]

    def backfill_recipient_counts(apps, schema):
        Broadcast = apps.get_model('msgs', 'Broadcast')
        Msg = apps.get_model('msgs', 'Msg')

        # get all broadcasts with 0 recipients
        for broadcast in Broadcast.objects.filter(recipient_count=0):
            # set to # of msgs
            broadcast.recipient_count = Msg.objects.filter(broadcast=broadcast).count()
            if broadcast.recipient_count > 0:
                broadcast.save()
                print "Updated %d to %d recipients" % (broadcast.id, broadcast.recipient_count)

    operations = [
        migrations.RunPython(backfill_recipient_counts)
    ]
