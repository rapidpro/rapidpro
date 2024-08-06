from .builtin import (
    ChannelDisconnectedIncidentType,
    ChannelOutdatedAppIncidentType,
    ChannelTemplatesFailedIncidentType,
    OrgFlaggedIncidentType,
    OrgSuspendedIncidentType,
    WebhooksUnhealthyIncidentType,
)

TYPES = {}


def register_incident_type(typ):
    """
    Registers an incident type
    """
    global TYPES

    assert typ.slug not in TYPES, f"type {typ.slug} is already registered"

    TYPES[typ.slug] = typ


register_incident_type(ChannelDisconnectedIncidentType())
register_incident_type(ChannelOutdatedAppIncidentType())
register_incident_type(OrgFlaggedIncidentType())
register_incident_type(OrgSuspendedIncidentType())
register_incident_type(WebhooksUnhealthyIncidentType())
register_incident_type(ChannelTemplatesFailedIncidentType())
