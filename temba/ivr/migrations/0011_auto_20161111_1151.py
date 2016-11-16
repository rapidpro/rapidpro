# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import migrations, models


class Migration(migrations.Migration):

    # dependencies = [
    #     ('ivr', '0010_auto_20160818_2150'),
    #     ('flows', '0072_auto_20160905_1537'),
    #     ('channels', '0039_channellog_request_time'),
    #     ('msgs', '0068_auto_20161017_1317'),
    #     ('triggers', '0006_auto_20161010_1633'),
    #     ('orgs', '0024_remove_invitation_host'),
    #     ('contacts', '0042_remove_exportcontactstask_host'),
    #     ('schedules', '0002_schedule_repeat_minute_of_hour'),
    #     ('values', '0007_auto_20160415_1328'),
    #     ('locations', '0005_auto_20160414_0642'),
    #     ('reports', '0001_initial'),
    #     ('campaigns', '0009_auto_20161019_1608'),
    #     ('auth_tweaks', '0001_initial'),
    #     ('api', '0008_webhookevent_resthook'),
    #     ('airtime', '0002_auto_20160806_0423')
    # ]

    dependencies = [
        ('ivr', '0010_auto_20160818_2150'),
        ('flows', '0073_auto_20161111_1534'),
    ]

    database_operations = [
        migrations.AlterModelTable('IVRCall', 'channels_ivrcall')
    ]

    state_operations = [
        migrations.DeleteModel('IVRCall')
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=database_operations,
            state_operations=state_operations)
    ]
