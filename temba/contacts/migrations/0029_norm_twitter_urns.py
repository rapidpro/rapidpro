# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations
from django.db.models import Func, F


def get_chosen_one(urns):
    """
    Selects the chosen Twitter URN out of a set with the same handle
    """
    chosen_urn = None

    # first try to find one with the last message
    last_msg_time = None
    for urn in urns:
        last_msg = urn.msgs.first()
        if last_msg and (last_msg_time is None or last_msg.created_on > last_msg_time):
            last_msg_time = last_msg.created_on
            chosen_urn = urn

    # possible they don't have messages so then use any
    if not chosen_urn:
        chosen_urn = urns[0]

    other_urns = [u for u in urns if u != chosen_urn]

    return chosen_urn, other_urns


def normalize_twitter_urns(apps, schema_editor):
    """
    Twitter URNs are case-insensitive so ContactURN.normalize_urn(..) converts URNs to lowercase - and this migration
    takes care of existing Twitter URNs.
    """
    Org = apps.get_model('orgs', 'Org')
    ContactURN = apps.get_model('contacts', 'ContactURN')
    Broadcast = apps.get_model('msgs', 'Broadcast')

    for org in Org.objects.all():
        # fetch and organize all Twitter URNs by lowercase handle
        urns_by_handle = defaultdict(list)
        for urn_id, handle in ContactURN.objects.filter(org=org, scheme='twitter').values_list('pk', 'path'):
            handle = handle.lower()
            urns_by_handle[handle].append(urn_id)

        if not urns_by_handle:
            continue

        print "Checking Twitter URNs for org #%d (%d URNs)..." % (org.pk, len(urns_by_handle))

        # find ones with duplicates
        duplicates = {handle: urn_ids for handle, urn_ids in urns_by_handle.iteritems() if len(urn_ids) > 1}

        print " > Found %d Twitter handles with duplicates" % (len(duplicates))

        for handle, urn_ids in duplicates.iteritems():
            urns = list(ContactURN.objects.filter(pk__in=urn_ids).select_related('contact').prefetch_related('msgs'))

            chosen_urn, other_urns = get_chosen_one(urns)

            for other_urn in other_urns:
                # attach messages for the other URNs to the last used URN
                num_msgs_moved = other_urn.msgs.update(contact_urn_id=chosen_urn.pk)
                if num_msgs_moved:
                    print " > Moved %d messages from handle %s to handle %s" % (num_msgs_moved, other_urn.path, chosen_urn.path)

                # remove in broadcasts with chosen URN
                broadcasts = Broadcast.objects.filter(urns=other_urn)
                for broadcast in broadcasts:
                    broadcast.urns.remove(other_urn)
                    broadcast.urns.add(chosen_urn)

                if broadcasts:
                    print " > Replaced URN in %d broadcasts from handle %s to handle %s" % (len(broadcasts), other_urn.path, chosen_urn.path)

                # delete this URN
                print " > Deleting URN #%d (%s)" % (other_urn.pk, other_urn.path)
                other_urn.delete()

        # finally with duplicates removed, update all URNs to use lowercase handles
        if urns_by_handle:
            twitter_urns = ContactURN.objects.filter(org=org, scheme='twitter')
            updated = twitter_urns.update(urn=Func(F('urn'), function='LOWER'), path=Func(F('path'), function='LOWER'))
            print " > Updated %d Twitter URNs to have lowercase handles" % updated


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0028_test_contact_index'),
    ]

    operations = [
        migrations.RunPython(normalize_twitter_urns)
    ]
