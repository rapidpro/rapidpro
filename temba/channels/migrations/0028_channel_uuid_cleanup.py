# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations, models
from uuid import uuid4


def move_non_uuids_to_bod(apps, schema_editor):
    Channel = apps.get_model('channels', 'Channel')
    updates_by_type = defaultdict(lambda: 0)

    for channel in Channel.objects.filter(channel_type__in=('NX', 'T', 'EX')):
        # ignore external channels whose UUIDs look valid
        if channel.channel_type == 'EX' and channel.uuid and len(channel.uuid) == 36:
            continue

        channel.bod = channel.uuid
        channel.uuid = unicode(uuid4())
        channel.save(update_fields=('bod', 'uuid'))

        updates_by_type[channel.channel_type] += 1

    # nullify UUIDs on inactive Android channels
    updates_by_type['A-i'] = Channel.objects.filter(channel_type='A', is_active=False).update(uuid=None)

    # gather up active channels by their UUID to find duplicates
    android_by_uuid = defaultdict(list)
    for android in Channel.objects.filter(channel_type='A', is_active=True).order_by('-last_seen'):
        android_by_uuid[android.uuid].append(android)

    # for each set of Android channels sharing an UUID, last seen one keeps the UUID, others nullified
    for uuid, androids in android_by_uuid.iteritems():
        if len(androids) > 1:
            non_chosen_one_ids = [a.pk for a in androids[1:]]
            Channel.objects.filter(pk__in=non_chosen_one_ids).update(uuid=None)
            updates_by_type['A-a'] += len(non_chosen_one_ids)

    if sum(updates_by_type.values()):
        print "Fixed UUIDs on channels:"
        print " - Nexmo: %d" % updates_by_type['NX']
        print " - Twilio: %d" % updates_by_type['T']
        print " - External: %d" % updates_by_type['EX']
        print " - Android (nullified inactive duplicates): %d" % updates_by_type['A-i']
        print " - Android (nullified active duplicates): %d" % updates_by_type['A-a']


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0027_auto_20151103_1014'),
    ]

    operations = [
        migrations.RunPython(move_non_uuids_to_bod),

        migrations.AlterField(
            model_name='channel',
            name='uuid',
            field=models.CharField(help_text='UUID for this channel', max_length=36, unique=True, null=True, verbose_name='UUID'),
        ),
    ]
