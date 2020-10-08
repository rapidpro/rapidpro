from typing import Dict

from .definition import *  # noqa


def migrate_definition(json_flow: Dict, flow=None):
    from temba.flows.models import Flow
    from . import migrations

    versions = get_versions_after(json_flow[Flow.VERSION])
    for version in versions:
        version_slug = version.replace(".", "_")
        migrate_fn = getattr(migrations, "migrate_to_version_%s" % version_slug, None)

        if migrate_fn:
            json_flow = migrate_fn(json_flow, flow)
            json_flow[Flow.VERSION] = version

        if version == Flow.FINAL_LEGACY_VERSION:
            break

    return json_flow
