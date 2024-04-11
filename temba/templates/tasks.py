import logging

import requests

from temba.channels.models import Channel
from temba.notifications.incidents.builtin import ChannelTemplatesFailedIncidentType
from temba.request_logs.models import HTTPLog
from temba.utils.crons import cron_task
from temba.utils.whatsapp import update_api_version

from .models import TemplateTranslation

logger = logging.getLogger(__name__)


@cron_task()
def refresh_templates():
    """
    Runs across all channels that have connected FB accounts and syncs the templates which are active.
    """

    num_refreshed, num_errored = 0, 0

    # get all active channels for types that use templates
    channel_types = [t.code for t in Channel.get_types() if t.template_type]
    channels = Channel.objects.filter(
        is_active=True, channel_type__in=channel_types, org__is_active=True, org__is_suspended=False
    )

    for channel in channels:
        # for channels which have version in their config, refresh it
        if channel.config.get("version"):
            update_api_version(channel)

        try:
            raw_templates = channel.type.fetch_templates(channel)

            TemplateTranslation.update_local(channel, raw_templates)

            num_refreshed += 1

            # if we have an ongoing template incident, end it
            ongoing = channel.incidents.filter(
                incident_type=ChannelTemplatesFailedIncidentType.slug, ended_on=None
            ).first()
            if ongoing:
                ongoing.end()

        except requests.RequestException:
            num_errored += 1

            # if last 5 sync attempts have been errors, create an incident
            recent_is_errors = list(
                channel.http_logs.filter(log_type=HTTPLog.WHATSAPP_TEMPLATES_SYNCED)
                .order_by("-id")
                .values_list("is_error", flat=True)[:5]
            )
            if len(recent_is_errors) >= 5 and all(recent_is_errors):
                ChannelTemplatesFailedIncidentType.get_or_create(channel)

        except Exception as e:
            logger.error(f"Error refreshing whatsapp templates: {str(e)}", exc_info=True)

    return {"refreshed": num_refreshed, "errored": num_errored}
