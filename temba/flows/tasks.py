import os
import logging
import time
import boto3

from datetime import datetime, timedelta

import iso8601
import pytz
from django_redis import get_redis_connection

from django.conf import settings
from django.utils import timezone
from django.utils.timesince import timesince

from celery.task import task

from temba.orgs.models import Org
from temba.utils.celery import nonoverlapping_task

from .models import (
    ExportFlowImagesTask,
    ExportFlowResultsTask,
    FlowCategoryCount,
    FlowNodeCount,
    FlowPathCount,
    FlowPathRecentRun,
    FlowRevision,
    FlowRun,
    FlowRunCount,
    FlowSession,
    FlowStartCount,
    MergeFlowsTask,
)

FLOW_TIMEOUT_KEY = "flow_timeouts_%y_%m_%d"
logger = logging.getLogger(__name__)


@task(track_started=True, name="send_email_action_task")
def send_email_action_task(org_id, recipients, subject, message):
    org = Org.objects.filter(pk=org_id, is_active=True).first()
    if org:
        org.email_action_send(recipients, subject, message)


@task(track_started=True, name="update_run_expirations_task")
def update_run_expirations_task(flow_id):
    """
    Update all of our current run expirations according to our new expiration period
    """
    for run in FlowRun.objects.filter(flow_id=flow_id, is_active=True):
        if run.path:
            last_arrived_on = iso8601.parse_date(run.path[-1]["arrived_on"])
            run.update_expiration(last_arrived_on)


@task(track_started=True, name="interrupt_flow_runs_task")
def interrupt_flow_runs_task(flow_id):
    runs = FlowRun.objects.filter(is_active=True, exit_type=None, flow_id=flow_id)
    FlowRun.bulk_exit(runs, FlowRun.EXIT_TYPE_INTERRUPTED)


@task(track_started=True, name="export_flow_results_task")
def export_flow_results_task(export_id):
    """
    Export a flow to a file and e-mail a link to the user
    """
    ExportFlowResultsTask.objects.select_related("org").get(id=export_id).perform()


@task(track_started=True, name="download_flow_images_task")
def download_flow_images_task(id):
    """
    Download flow images to a zip file and e-mail a link to the user
    """
    export_task = ExportFlowImagesTask.objects.filter(pk=id).first()
    if export_task:
        export_task.perform()


@nonoverlapping_task(
    track_started=True, name="squash_flowpathcounts", lock_key="squash_flowpathcounts", lock_timeout=7200
)
def squash_flowpathcounts():
    FlowPathCount.squash()


@nonoverlapping_task(
    track_started=True, name="squash_flowruncounts", lock_key="squash_flowruncounts", lock_timeout=7200
)
def squash_flowruncounts():
    FlowNodeCount.squash()
    FlowRunCount.squash()
    FlowCategoryCount.squash()
    FlowPathRecentRun.prune()
    FlowStartCount.squash()


@nonoverlapping_task(track_started=True, name="trim_flow_revisions")
def trim_flow_revisions():
    start = timezone.now()

    # get when the last time we trimmed was
    r = get_redis_connection()
    last_trim = r.get(FlowRevision.LAST_TRIM_KEY)
    if not last_trim:
        last_trim = 0

    last_trim = datetime.utcfromtimestamp(int(last_trim)).astimezone(pytz.utc)
    count = FlowRevision.trim(last_trim)

    r.set(FlowRevision.LAST_TRIM_KEY, int(timezone.now().timestamp()))

    elapsed = timesince(start)
    print(f"Trimmed {count} flow revisions since {last_trim} in {elapsed}")


@nonoverlapping_task(track_started=True, name="trim_flow_sessions")
def trim_flow_sessions():
    """
    Cleanup old flow sessions
    """
    threshold = timezone.now() - timedelta(days=settings.FLOW_SESSION_TRIM_DAYS)
    num_deleted = 0
    start = timezone.now()

    print(f"Deleting flow sessions which ended before {threshold.isoformat()}...")

    while True:
        session_ids = list(FlowSession.objects.filter(ended_on__lte=threshold).values_list("id", flat=True)[:1000])
        if not session_ids:
            break

        # detach any flows runs that belong to these sessions
        FlowRun.objects.filter(session_id__in=session_ids).update(session_id=None)

        FlowSession.objects.filter(id__in=session_ids).delete()
        num_deleted += len(session_ids)

        if num_deleted % 10000 == 0:  # pragma: no cover
            print(f" > Deleted {num_deleted} flow sessions")

    elapsed = timesince(start)
    print(f"Deleted {num_deleted} flow sessions which ended before {threshold.isoformat()} in {elapsed}")


@task(track_started=True, name="delete_flowimage_downloaded_files")
def delete_flowimage_downloaded_files():
    print("> Running garbage collector for Flow Images zip files")
    counter_files = 0
    start = time.time()

    s3 = (
        boto3.resource(
            "s3", aws_access_key_id=settings.AWS_ACCESS_KEY_ID, aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY
        )
        if settings.DEFAULT_FILE_STORAGE == "storages.backends.s3boto3.S3Boto3Storage"
        else None
    )
    download_tasks = (
        ExportFlowImagesTask.objects.filter(cleaned=False, file_downloaded=True).only("id").order_by("created_on")
    )
    for item in download_tasks:
        try:
            file_path = item.file_path
            if s3 and settings.AWS_BUCKET_DOMAIN in file_path:
                key = file_path.replace("https://%s/" % settings.AWS_BUCKET_DOMAIN, "")
                obj = s3.Object(settings.AWS_STORAGE_BUCKET_NAME, key)
                obj.delete()
            else:
                expected_fpath = file_path.replace(settings.MEDIA_URL, "")
                file_path = os.path.join(settings.MEDIA_ROOT, expected_fpath)
                os.remove(file_path)
            item.cleaned = True
            item.save(update_fields=["cleaned"])
            counter_files += 1
        except Exception:
            pass
    print("> Garbage collection finished in %0.3fs for %s file(s)" % (time.time() - start, counter_files))


@task(track_started=True, name="merge_flows")
def merge_flows_task(uuid):
    task = MergeFlowsTask.objects.filter(uuid=uuid).first()
    if task:
        task.process_merging()
