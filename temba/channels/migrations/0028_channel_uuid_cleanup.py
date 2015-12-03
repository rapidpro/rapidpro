# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import temba.utils.models

from collections import defaultdict
from django.db import migrations, models
from uuid import uuid4


def cleanup_channel_uuids(apps, schema_editor):
    """
    Cleans up channel UUIDs. For channels overriding UUID to hold phone numbers, moves these to bod field. For Android
    channels fixes lack of uniqueness by randomizing UUIDs in the case of duplicates.
    """
    Channel = apps.get_model('channels', 'Channel')
    updates_by_type = defaultdict(int)

    # firstly deal with channels who potentially override UUID
    for channel in Channel.objects.filter(channel_type__in=('NX', 'T', 'EX')):
        # ignore external channels whose UUIDs look valid
        if channel.channel_type == 'EX' and channel.uuid and len(channel.uuid) == 36:
            continue

        # put old UUID in BOD and generate new UUID
        old_uuid = channel.uuid
        new_uuid = unicode(uuid4())
        Channel.objects.filter(pk=channel.pk).update(uuid=new_uuid, bod=old_uuid)

        print 'Channel #%d: "%s" -> "%s" (old moved to BOD)' % (channel.pk, old_uuid if old_uuid else 'NULL', new_uuid)

        updates_by_type[channel.channel_type] += 1

    def set_random_uuids(channels):
        for ch in channels:
            old_uuid = ch.uuid
            new_uuid = unicode(uuid4())
            Channel.objects.filter(pk=ch.pk).update(uuid=new_uuid)

            print 'Channel #%d: "%s" -> "%s"' % (ch.pk, old_uuid if old_uuid else 'NULL', new_uuid)

    # random UUIDs on inactive Android channels
    inactive_android = list(Channel.objects.filter(channel_type='A', is_active=False))
    set_random_uuids(inactive_android)

    # gather up active channels by their UUID to find duplicates
    android_by_uuid = defaultdict(list)
    for android in Channel.objects.filter(channel_type='A', is_active=True).order_by('-last_seen'):
        android_by_uuid[android.uuid].append(android)

    # for each set of Android channels sharing an UUID, last seen one keeps the UUID
    for uuid, androids in android_by_uuid.iteritems():
        if len(androids) > 1:
            non_chosen_ones = androids[1:]

            # non-chosen ones get random UUIDs
            set_random_uuids(non_chosen_ones)

            updates_by_type['A'] += len(non_chosen_ones)

    # finally any channel left with null UUID gets a random UUID
    null_uuids = list(Channel.objects.filter(uuid=None))
    set_random_uuids(null_uuids)

    if sum(updates_by_type.values()):
        print "Channel UUID cleanup summary:"
        print " - Nexmo: %d" % updates_by_type['NX']
        print " - Twilio: %d" % updates_by_type['T']
        print " - External: %d" % updates_by_type['EX']
        print " - Android (inactive): %d" % len(inactive_android)
        print " - Android (active duplicates): %d" % updates_by_type['A']
        print " - Any other null UUIDs: %d" % len(null_uuids)


class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0027_auto_20151103_1014'),
        ('orgs', '0014_auto_20151027_1242'),
    ]

    operations = [
        migrations.RunPython(cleanup_channel_uuids),

        migrations.AlterField(
            model_name='channel',
            name='uuid',
            field=models.CharField(default=temba.utils.models.generate_uuid, max_length=36, help_text='The unique identifier for this object', unique=True, verbose_name='Unique Identifier', db_index=True),
        ),
    ]
