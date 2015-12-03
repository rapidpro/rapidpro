# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations, models
from uuid import uuid4


def cleanup_channel_uuids(apps, schema_editor):
    """
    Cleans up channel UUIDs. For channels overriding UUID to hold phone numbers, moves these to bod field. For Android
    channels fixes lack of uniqueness by randomizing UUIDs in the case of duplicates.
    """
    Channel = apps.get_model('channels', 'Channel')
    updates_by_type = defaultdict(lambda: 0)

    def set_random_uuids(channels):
        for ch in channels:
            Channel.objects.filter(pk=ch.pk).update(uuid=unicode(uuid4()))

    for channel in Channel.objects.filter(channel_type__in=('NX', 'T', 'EX')):
        # ignore external channels whose UUIDs look valid
        if channel.channel_type == 'EX' and channel.uuid and len(channel.uuid) == 36:
            continue

        channel.bod = channel.uuid
        channel.uuid = unicode(uuid4())
        channel.save(update_fields=('bod', 'uuid'))

        updates_by_type[channel.channel_type] += 1

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
        print "Fixed UUIDs on channels:"
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
            field=models.CharField(help_text='UUID for this channel', max_length=36, unique=True, null=False, verbose_name='UUID'),
        ),
    ]
