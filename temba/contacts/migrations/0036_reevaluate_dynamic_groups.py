# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations
from temba.contacts import search


def reevaluate_dynamic_groups(apps, schema_editor):
    from temba.contacts.models import Contact, ContactGroup

    for group in ContactGroup.all_groups.exclude(query=None).select_related('org'):
        org = group.org

        # evaluate group using search query
        qualifiers_qs = Contact.objects.filter(org=org, is_blocked=False, is_active=True, is_test=False)
        qualifiers_qs, is_complex = search.contact_search(org, group.query, qualifiers_qs)
        qualifier_ids = set(qualifiers_qs.values_list('pk', flat=True))

        # get currently stored members
        member_ids = set(group.contacts.all().values_list('pk', flat=True))

        if qualifier_ids != member_ids:
            print("Fixing member inconsistency for dynamic group '%s' [%d] in org '%s' [%d]..."
                  % (group.name, group.pk, org.name, org.pk))
            print(" > Group set contains %d contacts (count field is %d)" % (len(member_ids), group.count))
            print(" > Query '%s' returns %d contacts" % (group.query, len(qualifier_ids)))

            missing_ids = qualifier_ids - member_ids
            extra_ids = member_ids - qualifier_ids

            group.contacts.add(*missing_ids)
            group.contacts.remove(*extra_ids)

            print (" > Added %d missing contacts and removed %d extra contacts" % (len(missing_ids), len(extra_ids)))


class Migration(migrations.Migration):

    dependencies = [
        ('contacts', '0035_auto_20160414_0642'),
        ('orgs', '0019_org_surveyor_password'),
        ('orgs', '0022_auto_20160815_1726')
    ]

    operations = [
        migrations.RunPython(reevaluate_dynamic_groups)
    ]
