from . import get_versions_after, migrations


def migrate(org, exported_json, same_site, version):
    for version in get_versions_after(version):
        version_slug = version.replace(".", "_")
        migrate_fn = getattr(migrations, "migrate_export_to_version_%s" % version_slug, None)

        if migrate_fn:
            exported_json = migrate_fn(exported_json, org, same_site)

            # update the version of migrated flows
            flows = []
            for sub_flow in exported_json.get("flows", []):
                sub_flow["version"] = version
                flows.append(sub_flow)

            exported_json["flows"] = flows

        else:
            migrate_fn = getattr(migrations, "migrate_to_version_%s" % version_slug, None)
            if migrate_fn:
                flows = []
                for json_flow in exported_json.get("flows", []):
                    json_flow = migrate_fn(json_flow, None)

                    flows.append(json_flow)

                exported_json["flows"] = flows

        # update each flow's version number
        for json_flow in exported_json.get("flows", []):
            json_flow["version"] = version

    return exported_json
