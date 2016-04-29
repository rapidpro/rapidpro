# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from collections import defaultdict
from django.db import migrations


ROLES = ('administrators', 'editors', 'viewers', 'surveyors')

ROLE_TOKEN_GROUPS = {
    'administrators': ("Administrators", "Editors", "Surveyors"),
    'editors': ("Editors", "Surveyors"),
    'viewers': (),
    'surveyors': ("Surveyors",)
}


def fix_org_groups(apps, schema_editor):
    Org = apps.get_model('orgs', 'Org')

    for org in Org.objects.all():
        org_user_roles = defaultdict(list)

        # get the roles for all users in this org
        for role in ROLES:
            for user in getattr(org, role).all():
                org_user_roles[user].append(role)

        for user, user_roles in org_user_roles.iteritems():
            # fix users who have more than one role
            if len(user_roles) > 1:
                keep_role = user_roles[0]
                for remove_role in user_roles[1:]:
                    getattr(org, remove_role).remove(user)
                    print("Removed user '%s' [%d] from %s role in org '%s' [%d] as they have %s role"
                          % (user.username, user.pk, remove_role, org.name, org.pk, keep_role))

        # delete API tokens where users no longer have that role
        for token in org.api_tokens.select_related('user', 'role'):
            user = token.user
            user_actual_role = org_user_roles.get(user, [])[0]
            user_allowed_groups = ROLE_TOKEN_GROUPS.get(user_actual_role, [])

            if token.role.name not in user_allowed_groups:
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
