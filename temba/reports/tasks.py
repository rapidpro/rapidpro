import logging
import psycopg2
from celery.task import task

from django import db
from django.db.models import Max, F, Q
from django.db.models.functions import Greatest
from django.utils.timezone import now as tz_now

from .models import DataCollectionProcess, CollectedFlowResultsData
from ..orgs.models import Org

logger = logging.getLogger(__name__)


def process_collecting(state_obj, flows):
    processed, skipped, failed = 0, 0, 0
    for flow in flows:
        # collect flow data
        try:
            data = CollectedFlowResultsData.collect_results_data(flow)
            (processed, skipped) = (processed + 1, skipped) if data else (processed, skipped + 1)
        except (db.Error, psycopg2.Error) as exc:
            failed += 1
            logger.error("Exception in collect analytics data task: %s" % str(exc), exc_info=True)

        # try to update process state
        try:
            state_obj.flows_processed, state_obj.flows_skipped, state_obj.flows_failed = processed, skipped, failed
            state_obj.save(update_fields=["flows_processed", "flows_skipped", "flows_failed"])
        except (db.Error, psycopg2.Error):
            continue
    return processed, skipped, failed


@task(track_started=True, name="analytics__auto_collect_flow_results_data")
def automatically_collect_flow_results_data():
    for org in Org.objects.filter(analytics_config__isnull=False).only("id"):
        DataCollectionProcess.objects.filter(related_org=org, completed_on__isnull=True).update(completed_on=tz_now())
        filters = {
            "is_active": True,
            "is_system": False,
            "is_archived": False,
        }
        flows = (
            org.analytics_config.flows.filter(**filters)
            .annotate(last_updated=Greatest(Max("runs__modified_on"), F("modified_on")))
            .filter(Q(aggregated_results__isnull=True) | Q(aggregated_results__last_updated__lt=F("last_updated")))
            .only("metadata")
        )
        processing_state = DataCollectionProcess.objects.create(
            start_type=DataCollectionProcess.TYPE_AUTO,
            flows_total=len(flows),
            related_org_id=org.id,
        )
        processed, skipped, failed = process_collecting(processing_state, flows)
        processing_state.flows_processed, processing_state.flows_skipped, processing_state.flows_failed = (
            processed,
            skipped,
            failed,
        )
        processing_state.completed_on = tz_now()
        processing_state.save()


@task(track_started=True, name="analytics__collect_flow_results_data")
def manually_collect_flow_results_data(processing_state_id, flow_ids: list = None):
    try:
        processing_state = DataCollectionProcess.objects.get(id=processing_state_id)
        flows = processing_state.related_org.flows.filter(id__in=flow_ids).only("metadata")
        processed, skipped, failed = process_collecting(processing_state, flows)
        processing_state.flows_processed, processing_state.flows_skipped, processing_state.flows_failed = (
            processed,
            skipped,
            failed,
        )
        processing_state.completed_on = tz_now()
        processing_state.save()
    except DataCollectionProcess.DoesNotExist:
        pass
