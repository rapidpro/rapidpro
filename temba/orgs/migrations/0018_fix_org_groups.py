# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations


GROUP_SETS = ('administrators', 'editors', 'viewers', 'surveyors')

# API roles that a user is allowed to have if they belong to a given org group set
ALLOWED_API_ROLES = {
    'administrators': {"Administrators", "Editors", "Surveyors"},
    'editors': {"Editors", "Surveyors"},
    'viewers': set(),
    'surveyors': {"Surveyors"}
}


def fix_org_groups(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')

    for org in Org.objects.all():
        user_memberships = defaultdict(list)
        user_fixed_memberships = {}

        # get the group memberships for all users in this org
        for group_set in GROUP_SETS:
            for user in getattr(org, group_set).all():
                user_memberships[user].append(group_set)

        for user, memberships in user_memberships.iteritems():
            keep_group = memberships[0]
            user_fixed_memberships[user] = keep_group

            # fix users who are members of more than one group
            if len(memberships) > 1:
                for leave_group in memberships[1:]:
                    getattr(org, leave_group).remove(user)
                    print("Removed user '%s' [%d] from group %s in org '%s' [%d] as they are in %s"
                          % (user.username, user.pk, leave_group, org.name, org.pk, keep_group))

        # delete API tokens where users don't have a valid API role. They may have one at one point or may have
        # requested an API token in a group which can't use the API, e.g. Viewers
        for token in org.api_tokens.select_related('user', 'role'):
            user = token.user
            user_actual_group = user_fixed_memberships.get(user)
            user_allowed_roles = ALLOWED_API_ROLES.get(user_actual_group, [])

            if token.role.name not in user_allowed_roles:
                token.delete()
                print("Deleted token %s with role %s for user '%s' [%d] in org '%s' [%d]"
                      % (token.key, token.role.name, user.username, user.pk, org.name, org.pk))


class Migration(migrations.Migration):

    dependencies = [
        ('orgs', '0017_auto_20160301_0513'),
    ]

    operations = [
        migrations.RunPython(fix_org_groups)
    ]
