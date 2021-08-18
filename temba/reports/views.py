import json
import traceback
from itertools import groupby

import requests
from django.contrib.postgres.aggregates import ArrayAgg
from django.core.exceptions import ObjectDoesNotExist
from django.conf import settings
from django.db.models import F
from django.utils.timezone import now as tz_now
from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response as APIResponse
from smartmin.views import SmartCRUDL, SmartTemplateView
from temba.orgs.views import OrgPermsMixin
from .models import Report, DataCollectionProcess
from .tasks import manually_collect_flow_results_data
from ..contacts.models import ContactGroup
from ..flows.models import Flow, FlowRunCount


class ReportCRUDL(SmartCRUDL):
    actions = ("create", "delete", "analytics", "charts_data", "update_charts_data")
    model = Report

    class Create(OrgPermsMixin, APIView):
        permission = "reports.report_create"

        def post(self, request, *args, **kwargs):
            user = request.user
            org = user.get_org()
            try:
                report = Report.create_report(org, user, request.data)
            except Exception as e:  # pragma: needs cover
                traceback.print_exc(e)
                return APIResponse({"status": "error", "description": f"Error creating report: {e}"}, status=400)
            return APIResponse({"status": "success", "description": "Report Created", "report": report.as_json()})

    class Delete(OrgPermsMixin, APIView):
        permission = "reports.report_delete"

        def delete(self, request, *args, **kwargs):
            org = request.user.get_org()
            report_id = request.data.get("report_id")
            if all((org, report_id)):
                report = Report.objects.filter(org=org, id=report_id).first()
                if report:
                    report.delete()
            return APIResponse({}, status=status.HTTP_204_NO_CONTENT)

    class Analytics(OrgPermsMixin, SmartTemplateView):
        title = "Analytics"
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            org = self.request.user.get_org()
            dev_mode = getattr(settings, "EDITOR_DEV_MODE", False)
            prefix = "http://localhost:3000" if dev_mode else settings.STATIC_URL
            analytics_folder = "@greatnonprofits-nfp/temba-analytics/build"

            # get our list of assets to include
            scripts = []
            styles = []

            if dev_mode:  # pragma: no cover
                response = requests.get("http://localhost:3000/asset-manifest.json")
                data = response.json()
            else:
                with open(f"node_modules/{analytics_folder}/asset-manifest.json") as json_file:
                    data = json.load(json_file)

            def get_static_filename(filename):
                if dev_mode:
                    return f"{prefix}{filename}"
                return f"{settings.STATIC_URL}{analytics_folder}{filename}"

            for key, filename in data.get("files").items():
                # tack on our prefix for dev mode
                filename = get_static_filename(filename)

                # ignore precache manifest
                if key.startswith("precache-manifest") or key.startswith("service-worker"):
                    continue

                # css files
                if key.endswith(".css") and filename.endswith(".css"):
                    styles.append(filename)

                # javascript
                if key.endswith(".js") and filename.endswith(".js"):
                    scripts.append(filename)

            def flow_cast(flow):
                return {
                    "id": flow.id,
                    "text": flow.name,
                    "rules": [
                        {
                            "id": rule["key"],
                            "text": rule["name"],
                            "flow": flow.id,
                            "stats": {
                                "created_on": str(flow.created_on),
                            },
                        }
                        for rule in flow.metadata.get("results", [])
                    ],
                    "stats": {"created_on": str(flow.created_on), "runs": sum(FlowRunCount.get_totals(flow).values())},
                }

            flows = Flow.objects.filter(org_id=org.id, is_active=True, is_system=False, is_archived=False)
            flow_json = list(map(flow_cast, filter(lambda x: x.metadata.get("results"), flows)))

            groups = ContactGroup.user_groups.filter(org=org).order_by("name")
            groups_json = list(filter(lambda x: x is not None, [group.analytics_json() for group in groups]))

            reports = Report.objects.filter(is_active=True, org=org).order_by("title")
            reports_json = [report.as_json() for report in reports]

            return dict(
                analytics_context=json.dumps(
                    dict(
                        flows=flow_json,
                        groups=groups_json,
                        reports=reports_json,
                        data_status=DataCollectionProcess.get_last_collection_process_status(org),
                    )
                ),
                scripts=scripts,
                styles=styles,
            )

    class ChartsData(OrgPermsMixin, APIView):
        permission = "reports.report_read"

        def get_filters(self, org):
            # get contact ids to do filter
            values_filters = self.request.data.get("filters", {}).get("values", [])
            groups_filters = self.request.data.get("filters", {}).get("groups", [])
            if not any((values_filters, groups_filters)):
                return set(), False

            filter_ids = set()
            for flow_id, fields in groupby(values_filters, lambda x: x["field"]["flow"]):
                try:
                    flow = org.flows.get(id=flow_id)
                    results = flow.aggregated_results.data
                    for field in fields:
                        for category in field["categories"]:
                            filter_ids.update(results.get(field["field"]["rule"], {}).get(category, []))
                except ObjectDoesNotExist:
                    continue

            group_ids = [_id for _ids in groups_filters for _id in _ids]
            if group_ids:
                group_ids = org.contacts.filter(all_groups__id__in=group_ids).values_list("id", flat=True)
                filter_ids.update(group_ids)
            return filter_ids, True

        def get_segment(self, org):
            # get contact ids to do segment
            segment = self.request.data.get("segment")
            segment_ids = {}
            if not segment:
                return {}, False

            if segment["isGroupSegment"]:
                groups = {category["id"]: category["label"] for category in segment["categories"]}
                groups_contacts = (
                    org.contacts.annotate(group_id=F("all_groups__id"))
                    .filter(group_id__in=groups.keys())
                    .values("group_id")
                    .annotate(contact_ids=ArrayAgg("id"))
                )
                for group_contacts in groups_contacts:
                    label = groups.get(group_contacts["group_id"])
                    if label:
                        segment_ids[label] = set(group_contacts["contact_ids"])
            else:
                try:
                    flow = org.flows.get(id=segment["field"]["flow"])
                    results = flow.aggregated_results.data
                    results = results.get(segment["field"]["rule"])
                    for category in segment["categories"]:
                        segment_ids[category["label"]] = set(results.get(category["label"], []))
                except ObjectDoesNotExist:
                    pass
            return segment_ids, True

        def post(self, request, *args, **kwargs):
            user = self.request.user
            org = user.get_org()

            fields = self.request.data.get("fields", [])
            filtered_ids, do_filter = self.get_filters(org)
            segment_ids, do_segment = self.get_segment(org)
            charts_data = []

            def get_filtered_or_segmented_results(result_data, _segment_ids):
                categories = []
                for category in result_data.keys():
                    result_ids = result_data[category]
                    result_ids = [x for x in result_ids if x in filtered_ids] if do_filter else result_ids
                    result_ids = [x for x in result_ids if x in _segment_ids] if do_segment else result_ids
                    categories.append({"label": category, "count": len(result_ids)})
                return categories

            for flow_id, fields in groupby(fields, lambda x: x["id"]["flow"]):
                try:
                    flow = org.flows.get(id=flow_id)
                    results = flow.aggregated_results.data
                    if do_segment:
                        for field in fields:
                            segmented_chart_data = {
                                "id": field["id"],
                                "categories": [],
                            }
                            for _segment, _segment_ids in segment_ids.items():
                                segmented_chart_data["categories"].append(
                                    {
                                        "label": _segment,
                                        "categories": get_filtered_or_segmented_results(
                                            results.get(field["id"]["rule"], {}), _segment_ids
                                        ),
                                    }
                                )
                            charts_data.append(segmented_chart_data)
                    else:
                        for field in fields:
                            charts_data.append(
                                {
                                    "id": field["id"],
                                    "categories": get_filtered_or_segmented_results(
                                        results.get(field["id"]["rule"], {}), []
                                    ),
                                }
                            )
                except ObjectDoesNotExist:
                    continue
            return APIResponse(charts_data)

    class UpdateChartsData(OrgPermsMixin, APIView):
        permission = "reports.report_update"

        def post(self, request, *args, **kwargs):
            user = request.user
            org = user.get_org()
            flow = request.data.get("flow", None)
            only_status = request.data.get("onlyStatus", False)
            last_dc = DataCollectionProcess.get_last_collection_process_status(org)
            if only_status or not last_dc["completed"]:
                return APIResponse({"created": False, **last_dc})
            manually_collect_flow_results_data.delay(user.id, org.id, flow)
            return APIResponse({"created": True, "lastUpdated": tz_now(), "completed": False, "progress": 0.01})
