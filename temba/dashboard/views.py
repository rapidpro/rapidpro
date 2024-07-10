import time
from datetime import datetime, timedelta

from smartmin.views import SmartTemplateView

from django.db.models import Q, Sum
from django.http import JsonResponse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from temba.channels.models import Channel, ChannelCount
from temba.orgs.models import Org
from temba.orgs.views import OrgPermsMixin
from temba.utils.views import SpaMixin

flattened_colors = [
    "#335c81",
    "#65afff",
    "#1b2845",
    "#50ffb1",
    "#3c896d",
    "#546d64",
    "#ddb892",
    "#7f5539",
    "#9c6644",
    "#b5c99a",
    "#87986a",
    "#718355",
    "#ff5858",
    "#ff9090",
    "#ffb5b5",
    "#cc9c00",
    "#ffcb1f",
    "#ffe285",
]


class Home(SpaMixin, OrgPermsMixin, SmartTemplateView):
    """
    The main dashboard view
    """

    title = _("Dashboard")
    permission = "orgs.org_dashboard"
    template_name = "dashboard/home.html"
    menu_path = "/settings/dashboard"


class MessageHistory(OrgPermsMixin, SmartTemplateView):
    """
    Endpoint to expose message history since the dawn of time by day as JSON blob
    """

    permission = "orgs.org_dashboard"

    def render_to_response(self, context, **response_kwargs):
        orgs = []
        org = self.derive_org()
        if org:
            orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

        # get all our counts for that period
        daily_counts = ChannelCount.objects.filter(
            count_type__in=[
                ChannelCount.INCOMING_MSG_TYPE,
                ChannelCount.OUTGOING_MSG_TYPE,
            ]
        )

        daily_counts = daily_counts.filter(day__gt="2013-02-01").filter(day__lte=timezone.now())

        if orgs or not self.request.user.is_support:
            daily_counts = daily_counts.filter(channel__org__in=orgs)

        daily_counts = list(
            daily_counts.values("day", "count_type").order_by("day", "count_type").annotate(count_sum=Sum("count"))
        )

        msgs_in = []
        msgs_out = []
        epoch = datetime(1970, 1, 1)

        def get_timestamp(count_dict):
            """
            Gets a unix time that is highcharts friendly for a given day
            """
            count_date = datetime.fromtimestamp(time.mktime(count_dict["day"].timetuple()))
            return int((count_date - epoch).total_seconds() * 1000)

        def record_count(counts, day, count):
            """
            Records a count in counts list which is an ordered list of day, count tuples
            """
            is_new = True

            # if we have seen this one before, increment it
            if len(counts):
                last = counts[-1]
                if last and last[0] == day:
                    last[1] += count["count_sum"]
                    is_new = False

            # otherwise add it as a new count
            if is_new:
                counts.append([day, count["count_sum"]])

        msgs_total = []
        for count in daily_counts:
            direction = count["count_type"][0]
            day = get_timestamp(count)

            if direction == "I":
                record_count(msgs_in, day, count)
            elif direction == "O":
                record_count(msgs_out, day, count)

            # we create one extra series that is the combination of both in and out
            # so we can use that inside our navigator
            record_count(msgs_total, day, count)

        return JsonResponse(
            [
                dict(name="Incoming", type="column", data=msgs_in, showInNavigator=False),
                dict(name="Outgoing", type="column", data=msgs_out, showInNavigator=False),
            ],
            safe=False,
        )


class WorkspaceStats(OrgPermsMixin, SmartTemplateView):
    permission = "orgs.org_dashboard"

    def render_to_response(self, context, **response_kwargs):
        orgs = []
        org = self.derive_org()
        if org:
            orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

        min_date = self.request.GET.get("min", datetime(2013, 1, 1).timestamp())
        max_date = self.request.GET.get("max", datetime.now().timestamp())

        if min_date and max_date:
            min_date = datetime.utcfromtimestamp(float(min_date)).strftime("%Y-%m-%d")
            max_date = datetime.utcfromtimestamp(float(max_date)).strftime("%Y-%m-%d")

        # get all our counts for that period
        daily_counts = ChannelCount.objects.filter(
            count_type__in=[
                ChannelCount.INCOMING_MSG_TYPE,
                ChannelCount.OUTGOING_MSG_TYPE,
            ]
        )

        daily_counts = daily_counts.filter(day__gte=min_date).filter(day__lte=max_date)

        if orgs or not self.request.user.is_support:
            daily_counts = daily_counts.filter(channel__org__in=orgs)

        categories = []
        inbound = []
        outbound = []

        for org in orgs:
            org_daily_counts = list(
                daily_counts.filter(channel__org_id=org.id)
                .values("count_type")
                .order_by("count_type")
                .annotate(count_sum=Sum("count"))
            )

            # ignore orgs with no activity in this period
            if not org_daily_counts:
                continue

            inbound_count, outbound_count = 0, 0

            for count in org_daily_counts:
                if count["count_type"] == ChannelCount.INCOMING_MSG_TYPE:
                    inbound_count = count["count_sum"]
                elif count["count_type"] == ChannelCount.OUTGOING_MSG_TYPE:
                    outbound_count = count["count_sum"]

            categories.append(org.name)
            inbound.append(inbound_count)
            outbound.append(outbound_count)

        return JsonResponse(
            {
                "series": [{"name": "Incoming", "data": inbound}, {"name": "Outgoing", "data": outbound}],
                "categories": categories,
            }
        )


class RangeDetails(OrgPermsMixin, SmartTemplateView):
    """
    Intercooler snippet to show detailed information for a specific range
    """

    permission = "orgs.org_dashboard"
    template_name = "dashboard/range_details.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        end = timezone.now()
        begin = end - timedelta(days=30)
        begin = self.request.GET.get("begin", datetime.strftime(begin, "%Y-%m-%d"))
        end = self.request.GET.get("end", datetime.strftime(end, "%Y-%m-%d"))

        direction = self.request.GET.get("direction", "IO")

        if begin and end:
            orgs = []
            org = self.derive_org()
            if org:
                orgs = Org.objects.filter(Q(id=org.id) | Q(parent=org))

            count_types = []
            if "O" in direction:
                count_types = [ChannelCount.OUTGOING_MSG_TYPE, ChannelCount.OUTGOING_IVR_TYPE]

            if "I" in direction:
                count_types += [ChannelCount.INCOMING_MSG_TYPE, ChannelCount.INCOMING_IVR_TYPE]

            # get all our counts for that period
            daily_counts = (
                ChannelCount.objects.filter(count_type__in=count_types)
                .filter(day__gte=begin)
                .filter(day__lte=end)
                .exclude(channel__org=None)
            )
            if orgs:
                daily_counts = daily_counts.filter(channel__org__in=orgs)

            context["orgs"] = list(
                daily_counts.values("channel__org", "channel__org__name")
                .annotate(count_sum=Sum("count"))
                .order_by("-count_sum")[:12]
            )

            channel_types = (
                ChannelCount.objects.filter(count_type__in=count_types)
                .filter(day__gte=begin)
                .filter(day__lte=end)
                .exclude(channel__org=None)
            )

            if orgs or not self.request.user.is_support:
                channel_types = channel_types.filter(channel__org__in=orgs)

            channel_types = list(
                channel_types.values("channel__channel_type").annotate(count_sum=Sum("count")).order_by("-count_sum")
            )

            # populate the channel names
            pie = []
            for channel_type in channel_types[0:6]:
                channel_type["channel__name"] = Channel.get_type_from_code(channel_type["channel__channel_type"]).name
                pie.append(channel_type)

            other_count = 0
            for channel_type in channel_types[6:]:
                other_count += channel_type["count_sum"]

            if other_count:
                pie.append(dict(channel__name="Other", count_sum=other_count))

            context["channel_types"] = pie

            context["begin"] = datetime.strptime(begin, "%Y-%m-%d").date()
            context["end"] = datetime.strptime(end, "%Y-%m-%d").date()
            context["direction"] = direction

        return context
