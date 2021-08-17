from celery.task import task

from django.utils.timezone import now as tz_now
from django.db import OperationalError as DjangoOperationalError
from psycopg2._psycopg import OperationalError as PostgresOperationalError

from temba.flows.models import Flow
from .models import DataCollectionProcess, CollectedFlowResultsData


def process_collecting(state_obj, filters):
    for flow in Flow.objects.filter(**filters):
        try:
            data = CollectedFlowResultsData.collect_results_data(flow)
            attr = "flows_skipped" if data is None else "flows_processed"
            setattr(state_obj, attr, (getattr(state_obj, attr) + 1))
            state_obj.save(update_fields=["flows_processed", "flows_skipped"])
        except (DjangoOperationalError, PostgresOperationalError):
            attr = "flows_failed"
            setattr(state_obj, attr, (getattr(state_obj, attr) + 1))
            state_obj.save(update_fields=["flows_processed", "flows_skipped"])


@task(track_started=True, name="analytics__auto_collect_flow_results_data")
def automatically_collect_flow_results_data():
    filters = {
        "is_active": True,
        "is_system": False,
        "is_archived": False,
    }
    processing_state = DataCollectionProcess.objects.create(
        start_type=DataCollectionProcess.TYPE_AUTO,
        flows_total=Flow.objects.filter(**filters).count(),
    )
    process_collecting(processing_state, filters)
    processing_state.completed_on = tz_now()
    processing_state.save(update_fields=["completed_on"])


@task(track_started=True, name="analytics__collect_flow_results_data")
def manually_collect_flow_results_data(user_id, org_id, flow_id=None):
    filters = {
        "org_id": org_id,
        "is_active": True,
        "is_system": False,
        "is_archived": False,
    }
    filters.update({"id": flow_id} if flow_id else {})
    processing_state = DataCollectionProcess.objects.create(
        start_type=DataCollectionProcess.TYPE_MANUAL,
        flows_total=Flow.objects.filter(**filters).count(),
        started_by_id=user_id,
        related_org_id=org_id,
    )
    process_collecting(processing_state, filters)
    processing_state.completed_on = tz_now()
    processing_state.save(update_fields=["completed_on"])
