# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations
from django.db.models import Func, F


def normalize_twitter_urns(apps, schema_editor):
    """
    Twitter URNs are case-insensitive so ContactURN.normalize_urn(..) converts URNs to lowercase - and this migration
    takes care of existing Twitter URNs.
    """
    Org = apps.get_model('orgs', 'Org')
    ContactURN = apps.get_model('contacts', 'ContactURN')

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

            # calculate last used URN by looking at last message created
            last_used_urn = None
            last_msg_time = None
            for urn in urns:
                last_msg = urn.msgs.first()
                if last_msg and (last_msg_time is None or last_msg.created_on > last_msg_time):
                    last_msg_time = last_msg.created_on
                    last_used_urn = urn

            # possible they don't have messages so then use any
            if not last_used_urn:
                last_used_urn = urns[0]

            other_urns = [u for u in urns if u != last_used_urn]

            # attach messages for the other URNs to the last used URN
            for other_urn in other_urns:
                num_msgs_moved = other_urn.msgs.update(contact_urn_id=last_used_urn.pk)
                print " > Moved %d messages from handle %s to handle %s" % (num_msgs_moved, other_urn.path, last_used_urn.path)

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
