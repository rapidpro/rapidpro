# -*- coding: utf-8 -*-
from __future__ import unicode_literals

import json
from django.db import migrations, models


class Migration(migrations.Migration):

    def whitelist_orgs(apps, schema_editor):
        """
        Whitelists all existing non-suspended orgs from auto-suspension
        """
        Org = apps.get_model('orgs', 'Org')
        for org in Org.objects.all():
            if org.config:

                config = {}
                try:
                    config = json.loads(org.config)
                except:
                    pass

                status = config.get('STATUS', None)
                if status != 'suspended':
                    config['STATUS'] = 'whitelisted'
                    
                org.config = json.dumps(config)
                org.save()

    dependencies = [
        ('orgs', '0016_remove_squash'),
    ]

    operations = [
        migrations.RunPython(whitelist_orgs)
    ]
