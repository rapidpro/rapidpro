from __future__ import unicode_literals

import time
import six

from datetime import timedelta, datetime
from django.db.models import Count, Sum, Avg, StdDev, Q
from django.http import JsonResponse
from django.utils import timezone
from smartmin.views import SmartTemplateView
from temba.orgs.models import Org
from temba.channels.models import ChannelCount, Channel
from temba.orgs.views import OrgPermsMixin


class Home(SmartTemplateView):
    """
    The main dashboard view
    """
    permission = 'orgs.org_dashboard'
    template_name = 'dashboard/home.haml'


class MessageHistory(OrgPermsMixin, SmartTemplateView):
    """
    Endpoint to expose message history since the dawn of time by day as JSON blob
    """
    permission = 'orgs.org_dashboard'

    def render_to_response(self, context, **response_kwargs):

        is_support = self.request.user.groups.filter(name='Customer Support').first()

        orgs = []
        org_id = self.request.session.get('org_id', None)
        if org_id:
            org = Org.objects.filter(is_active=True, id=org_id).first()
            filter_org = self.request.GET.get("org")
            if filter_org:
                filter_org = Org.objects.filter(is_active=True, id=filter_org).first()
                if filter_org and filter_org.parent == org:
                    org = filter_org

            if org:
                orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

        # get all our counts for that period
        daily_counts = ChannelCount.objects.filter(count_type__in=[ChannelCount.INCOMING_MSG_TYPE,
                                                                   ChannelCount.OUTGOING_MSG_TYPE,
                                                                   ChannelCount.INCOMING_IVR_TYPE,
                                                                   ChannelCount.OUTGOING_IVR_TYPE]).filter(day__lt=timezone.now()).filter(day__gt='2013-02-01')

        if orgs or not is_support:
            daily_counts = daily_counts.filter(channel__org__in=orgs)

        daily_counts = list(daily_counts.values('day', 'count_type').order_by('day', 'count_type').annotate(count_sum=Sum('count')))

        msgs_in = []
        msgs_out = []
        epoch = datetime(1970, 1, 1)

        def get_timestamp(count_dict):
            count_date = datetime.fromtimestamp(time.mktime(count_dict['day'].timetuple()))
            return int((count_date - epoch).total_seconds() * 1000)

        totals = {}
        for count in daily_counts:
            direction = count['count_type'][0]
            day = get_timestamp(count)

            if direction == 'I':
                msgs_in.append([day, count['count_sum']])
            elif direction == 'O':
                msgs_out.append([day, count['count_sum']])

            totals[day] = totals.get(day, 0) + count['count_sum']

        # we create one extra series that is the combination of both in and out
        # so we can use that inside our navigator
        msgs_total = [(k, v) for k, v in six.iteritems(totals)]
        msgs_total = sorted(msgs_total, key=lambda x: x[0])

        return JsonResponse([
            dict(name="Incoming", type="column", data=msgs_in, showInNavigator=False),
            dict(name="Outgoing", type="column", data=msgs_out, showInNavigator=False),
            dict(name="Total", type="column", data=msgs_total, showInNavigator=True, showInLegend=False, visible=False),
        ], safe=False)


class RangeDetails(OrgPermsMixin, SmartTemplateView):
    """
    Intercooler snippet to show detailed information for a specific range
    """
    permission = 'orgs.org_dashboard'
    template_name = 'dashboard/range_details.haml'

    def get_context_data(self, **kwargs):
        context = super(RangeDetails, self).get_context_data(**kwargs)

        is_support = self.request.user.groups.filter(name='Customer Support').first()

        end = timezone.now()
        begin = end - timedelta(days=30)
        begin = self.request.GET.get("begin", datetime.strftime(begin, "%Y-%m-%d"))
        end = self.request.GET.get("end", datetime.strftime(end, "%Y-%m-%d"))

        direction = self.request.GET.get("direction")

        if begin and end:
            orgs = []
            org_id = self.request.session.get('org_id', None)
            if org_id:
                org = Org.objects.filter(is_active=True, id=org_id).first()
                filter_org = self.request.GET.get("org")
                if filter_org:
                    filter_org = Org.objects.filter(is_active=True, id=filter_org).first()
                    if filter_org and filter_org.parent == org:
                        org = filter_org
                if org:
                    orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

            count_types = []
            if 'O' in direction:
                count_types = [ChannelCount.OUTGOING_MSG_TYPE, ChannelCount.OUTGOING_IVR_TYPE]

            if 'I' in direction:
                count_types += [ChannelCount.INCOMING_MSG_TYPE, ChannelCount.INCOMING_IVR_TYPE]

            # get all our counts for that period
            daily_counts = ChannelCount.objects.filter(count_type__in=count_types).filter(day__gte=begin).filter(day__lte=end).exclude(channel__org=None)
            if orgs:
                daily_counts = daily_counts.filter(channel__org__in=orgs)

            context['orgs'] = list(daily_counts.values('channel__org', 'channel__org__name').order_by('-count_sum',).annotate(count_sum=Sum('count'))[:12])

            channel_types = ChannelCount.objects.filter(count_type__in=count_types).filter(day__gte=begin).filter(day__lte=end).exclude(channel__org=None)

            if orgs or not is_support:
                channel_types = channel_types.filter(channel__org__in=orgs)

            channel_types = list(channel_types.values('channel__channel_type').order_by('-count_sum', ).annotate(count_sum=Sum('count')))

            # populate the channel names
            pie = []
            for channel_type in channel_types[0:6]:
                channel_type["channel__name"] = Channel.get_type_from_code(channel_type["channel__channel_type"])
                pie.append(channel_type)

            other_count = 0
            for channel_type in channel_types[6:]:
                other_count += channel_type["count_sum"]

            if other_count:
                pie.append(dict(channel__name="Other", count_sum=other_count))
            context['channel_types'] = pie

            context['begin'] = datetime.strptime(begin, "%Y-%m-%d").date()
            context['end'] = datetime.strptime(end, "%Y-%m-%d").date()
            context['direction'] = direction

        return context