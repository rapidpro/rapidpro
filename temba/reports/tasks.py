from celery.task import task

from temba.flows.models import Flow
from .models import FlowResultsAggregation


@task(track_started=True, name="analytics__collect_aggregated_flow_results_data")
def collect_aggregated_flow_results_data():
    for flow in Flow.objects.filter(is_active=True, is_system=False, is_archived=False):
        FlowResultsAggregation.aggregate_flow_results_data(flow)
