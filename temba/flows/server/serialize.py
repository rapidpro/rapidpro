from mptt.utils import get_cached_trees

from django.db.models import Prefetch

from temba.values.constants import Value

VALUE_TYPE_NAMES = {c[0]: c[2] for c in Value.TYPE_CONFIG}
VALUE_TYPE_NAMES["N"] = "number"


def serialize_ref(obj):
    return {"uuid": str(obj.uuid), "name": obj.name or ""}


# TODO: don't believe we actually need this anymore
def serialize_flow(flow):
    """
    Migrates the given flow, returning None if the flow or any of its dependencies can't be run in
    goflow because of unsupported features.
    """
    from temba.flows.models import Flow

    current_revision = flow.get_current_revision()
    return current_revision.get_definition_json(to_version=Flow.GOFLOW_VERSION)


def serialize_channel(channel):
    from temba.channels.models import Channel

    serialized = {
        "uuid": str(channel.uuid),
        "name": channel.name or "",
        "address": channel.address,
        "schemes": channel.schemes,
        "roles": [Channel.ROLE_CONFIG[r] for r in channel.role],
    }

    if channel.parent_id:
        serialized["parent"] = serialize_ref(channel.parent)
    if channel.country:
        serialized["country"] = channel.country.code

    config = channel.config or {}
    match_prefixes = config.get(Channel.CONFIG_SHORTCODE_MATCHING_PREFIXES, [])
    if match_prefixes:
        serialized["match_prefixes"] = match_prefixes

    return serialized


def serialize_environment(org):
    return {
        "date_format": "DD-MM-YYYY" if org.date_format == "D" else "MM-DD-YYYY",
        "time_format": "tt:mm",
        "timezone": str(org.timezone),
        "default_language": org.primary_language.iso_code if org.primary_language else None,
        "allowed_languages": list(org.get_language_codes()),
        "default_country": org.get_country_code(),
        "redaction_policy": "urns" if org.is_anon else "none",
    }


def serialize_field(field):
    return {"key": field.key, "name": field.label, "value_type": VALUE_TYPE_NAMES[field.value_type]}


def serialize_group(group):
    return {"uuid": str(group.uuid), "name": group.name, "query": group.query}


def serialize_label(label):
    return {"uuid": str(label.uuid), "name": label.name}


def serialize_language(language):
    return {"iso": language.iso_code, "name": language.name}


def serialize_location_hierarchy(org):
    """
    Serializes a country as a location hierarchy, e.g.
    {
        "name": "Rwanda",
        "children": [
            {
                "name": "Kigali City",
                "aliases": ["Kigali", "Kigari"],
                "children": [
                    ...
                ]
            }
        ]
    }
    """
    from temba.locations.models import BoundaryAlias

    queryset = org.country.get_descendants(include_self=True).prefetch_related(
        Prefetch("aliases", queryset=BoundaryAlias.objects.filter(org=org))
    )

    def _serialize_node(node):
        rendered = {"name": node.name}

        aliases = [a.name for a in node.aliases.all()]
        if aliases:
            rendered["aliases"] = aliases

        children = node.get_children()
        if children:
            rendered["children"] = []
            for child in node.get_children():
                rendered["children"].append(_serialize_node(child))
        return rendered

    return [_serialize_node(node) for node in get_cached_trees(queryset)][0]


def serialize_message(msg):
    serialized = {"uuid": str(msg.uuid), "text": msg.text}

    if msg.contact_urn_id:
        serialized["urn"] = msg.contact_urn.urn
    if msg.channel_id:
        serialized["channel"] = serialize_ref(msg.channel)
    if msg.attachments:
        serialized["attachments"] = msg.attachments

    return serialized


def serialize_resthook(resthook):
    return {"slug": resthook.slug, "subscribers": [s.target_url for s in resthook.subscribers.all()]}
