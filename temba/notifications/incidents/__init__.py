from .builtin import OrgFlaggedIncidentType, WebhooksUnhealthyIncidentType

TYPES = {}


def register_incident_type(typ):
    """
    Registers an incident type
    """
    global TYPES

    assert typ.slug not in TYPES, f"type {typ.slug} is already registered"

    TYPES[typ.slug] = typ


register_incident_type(OrgFlaggedIncidentType())
register_incident_type(WebhooksUnhealthyIncidentType())
