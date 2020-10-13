from typing import Dict

from packaging.version import Version

VERSIONS = [
    "1",
    "2",
    "3",
    "4",
    "5",
    "6",
    "7",
    "8",
    "9",
    "10",
    "10.1",
    "10.2",
    "10.3",
    "10.4",
    "11.0",
    "11.1",
    "11.2",
    "11.3",
    "11.4",
    "11.5",
    "11.6",
    "11.7",
    "11.8",
    "11.9",
    "11.10",
    "11.11",
    "11.12",
]


def get_versions_after(version_number):
    # older flows had numeric versions, lets make sure we are dealing with strings
    version_number = Version(f"{version_number}")
    return [v for v in VERSIONS if Version(v) > version_number]


def migrate_definition(json_flow: Dict, flow=None):
    from . import migrations

    versions = get_versions_after(json_flow["version"])
    for version in versions:
        version_slug = version.replace(".", "_")
        migrate_fn = getattr(migrations, "migrate_to_version_%s" % version_slug, None)

        if migrate_fn:
            json_flow = migrate_fn(json_flow, flow)
            json_flow["version"] = version

    return json_flow
