import logging

from django.conf import settings

from temba.contacts.models import Contact
from temba.flows.server.client import FlowServerException, get_client
from temba.utils import analytics

from .utils import reduce_event

logger = logging.getLogger(__name__)


class Trial:
    """
    A trial of running a campaign message flow in the flowserver
    """

    def __init__(self, flow, contact, campaign_event):
        self.flow = flow
        self.contact = contact
        self.campaign_event = campaign_event
        self.run = None
        self.differences = None


def maybe_start(flow, contact_id, campaign_event):
    """
    Starts a trial of a campaign message flow if a flowserver is configured
    """
    if not settings.FLOW_SERVER_URL or settings.FLOW_SERVER_TRIAL == "off":
        return None

    try:
        return Trial(flow, Contact.objects.get(id=contact_id), campaign_event)
    except Exception:
        logger.error(
            f"Unable to start trial for contact #{contact_id} in message flow {str(flow.uuid)}", exc_info=True
        )
        return None


def end(trial, run):
    """
    Ends a trial of a campaign message flow
    """
    try:
        session = run_flow(trial.flow, trial.contact, trial.campaign_event)
        trial.run = run
        trial.differences = compare(session, run)

        if trial.differences:
            report_failure(trial)
            return False
        else:
            report_success(trial)
            return True

    except FlowServerException as e:
        logger.error("Trial message flow in flowserver caused server error", extra=e.as_json())
        return False
    except Exception as e:
        logger.error(f"Exception during trial message flow of run {str(run.uuid)}: {str(e)}", exc_info=True)
        return False


def report_success(trial):  # pragma: no cover
    """
    Reports a trial success... essentially a noop but useful for mocking in tests
    """
    logger.info(f"Flowserver trial message flow for run {str(trial.run.uuid)} succeeded")

    analytics.gauge("temba.flowserver_trial.campaign_pass")


def report_failure(trial):  # pragma: no cover
    """
    Reports a trial failure to sentry
    """
    logger.error(
        "trial message flow in flowserver produced different output",
        extra={
            "org": trial.flow.org.name,
            "flow": {"uuid": str(trial.flow.uuid), "name": trial.flow.name},
            "run_id": trial.run.id,
            "differences": trial.differences,
        },
    )

    analytics.gauge("temba.flowserver_trial.campaign_fail")


def run_flow(flow, contact, campaign_event):
    client = get_client()

    # build request to flow server
    request = client.request_builder(flow.org).asset_server().set_config("disable_webhooks", True)

    if settings.TESTING:
        request.include_all()

    return request.start_by_campaign(contact, flow, campaign_event).session


def compare(session, actual_run):
    if session["status"] != "completed":
        return {"problem": "Message flows should always produce a completed session"}
    if len(session["runs"]) != 1:
        return {"problem": "Message flows should always produce a session with a single run"}

    actual_events = actual_run.events or []

    new_engine_run = session["runs"][0]
    new_engine_events = new_engine_run.get("events", [])

    new = [reduce_event(e) for e in new_engine_events]
    old = [reduce_event(e) for e in actual_events]
    if new != old:
        return {"new": new, "old": old}

    return None
