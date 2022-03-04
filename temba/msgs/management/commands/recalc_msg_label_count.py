from celery import shared_task
from django.core.management import BaseCommand
from django.db.models import Count, Case, When, IntegerField, Q

from temba.msgs.models import Msg, OUTGOING, INCOMING, WIRED, SENT, DELIVERED, IVR, FLOW, SystemLabelCount, SystemLabel
from temba.orgs.models import Org


@shared_task(track_started=True, name="get_calculated_values")
def get_calculated_values(org_id):  # pragma: no cover
    label_mapping = dict(
        text_flows=SystemLabel.TYPE_FLOWS,
        voice_flows=SystemLabel.TYPE_FLOW_VOICE,
        sent_text=SystemLabel.TYPE_SENT,
        sent_voice=SystemLabel.TYPE_SENT_VOICE,
    )

    results = Msg.objects.filter(org=org_id).aggregate(
        text_flows=Count(
            Case(
                When(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, msg_type=FLOW, then=1),
                output_field=IntegerField(),
            )
        ),
        voice_flows=Count(
            Case(
                When(direction=INCOMING, visibility=Msg.VISIBILITY_VISIBLE, msg_type=IVR, then=1),
                output_field=IntegerField(),
            )
        ),
        sent_voice=Count(
            Case(
                When(
                    direction=OUTGOING,
                    visibility=Msg.VISIBILITY_VISIBLE,
                    status__in=(WIRED, SENT, DELIVERED),
                    msg_type=IVR,
                    then=1,
                ),
                output_field=IntegerField(),
            )
        ),
        sent_text=Count(
            Case(
                When(
                    Q(direction=OUTGOING)
                    & Q(visibility=Msg.VISIBILITY_VISIBLE)
                    & Q(status__in=(WIRED, SENT, DELIVERED))
                    & ~Q(msg_type=IVR),
                    then=1,
                ),
                output_field=IntegerField(),
            )
        ),
    )

    for key, count in results.items():
        label_type = label_mapping[key]
        update_system_label_counts(count, org_id, label_type)


def update_system_label_counts(count, org_id, label_type):  # pragma: no cover
    try:
        obj = SystemLabelCount.objects.get(org_id=org_id, label_type=label_type)
        obj.count = count
        obj.save()
    except SystemLabelCount.DoesNotExist:
        if count > 0:
            obj = SystemLabelCount(org_id=org_id, label_type=label_type, count=count)
            obj.save()


class Command(BaseCommand):  # pragma: no cover
    def add_arguments(self, parser):
        parser.add_argument(
            "--orgs",
            type=str,
            action="store",
            dest="orgs",
            default=None,
            help="comma separated list of IDs of orgs to re-calculate its system label counts",
        )

    def handle(self, *args, **options):
        org_list = options["orgs"]
        if org_list is not None:
            orgs = org_list.split(",")
        else:
            orgs = list(Org.objects.values_list('id', flat=True))

        for org_id in orgs:
            get_calculated_values.delay(int(org_id))
