# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.db import models, migrations
from django.db.models import Count

class Migration(migrations.Migration):

    dependencies = [
        ('channels', '0019_update_channellog_triggers'),
    ]

    def calculate_counts(apps, schema_editor):
        """
        Iterate across all our channels, calculate our message counts for each category
        """
        ChannelCount = apps.get_model('channels', 'ChannelCount')
        Channel = apps.get_model('channels', 'Channel')
        Msg = apps.get_model('msgs', 'Msg')

        def add_daily_counts(count_channel, count_type, count_totals):
            for daily_count in count_totals:
                print "Adding %d - %s - %s" % (count_channel.id, count_type, str(daily_count))

                ChannelCount.objects.create(channel=channel, count_type=count_type,
                                            day=daily_count['created'], count=daily_count['count'])

        for channel in Channel.objects.all():
            # remove previous counts
            ChannelCount.objects.filter(channel=channel, count_type__in=['IM', 'OM', 'IV', 'OV']).delete()

            # incoming msgs
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='I')\
                                      .exclude(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'IM', daily_counts)

            # outgoing msgs
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='O')\
                                      .exclude(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'OM', daily_counts)

            # incoming voice
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='I')\
                                      .filter(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'IV', daily_counts)

            # outgoing voice
            daily_counts = Msg.objects.filter(channel=channel, contact__is_test=False, direction='O')\
                                      .filter(msg_type='V')\
                                      .extra({'created': "date(msgs_msg.created_on)"})\
                                      .values('created')\
                                      .annotate(count=Count('id'))\
                                      .order_by('created')
            add_daily_counts(channel, 'OV', daily_counts)

    operations = [
        migrations.RunPython(
            calculate_counts,
        ),
    ]
