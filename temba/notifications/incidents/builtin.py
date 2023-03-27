from ..models import Incident, IncidentType


class OrgFlaggedIncidentType(IncidentType):
    """
    Org has been flagged due to suspicious activity.
    """

    slug = "org:flagged"

    @classmethod
    def get_or_create(cls, org):
        """
        Creates a flagged incident if one is not already ongoing
        """
        return Incident.get_or_create(org, OrgFlaggedIncidentType.slug, scope="")


class OrgSuspendedIncidentType(IncidentType):
    """
    Org has been suspended.
    """

    slug = "org:suspended"

    @classmethod
    def get_or_create(cls, org):
        """
        Creates a suspended incident if one is not already ongoing
        """
        return Incident.get_or_create(org, OrgSuspendedIncidentType.slug, scope="")


class WebhooksUnhealthyIncidentType(IncidentType):
    """
    Webhook calls from flows have been taking too long to respond for a period of time - created by mailroom.
    """

    slug = "webhooks:unhealthy"
