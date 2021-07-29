import json
import traceback

from django.shortcuts import reverse
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from smartmin.views import SmartCRUDL, SmartCreateView, SmartTemplateView, SmartReadView
from temba.orgs.views import OrgPermsMixin
from .models import Report
from ..contacts.models import ContactField, ContactGroup
from ..flows.models import Flow, FlowRunCount, FlowRevision


class ReportCRUDL(SmartCRUDL):
    actions = ("create", "analytics", "results", "choropleth")
    model = Report

    class Create(OrgPermsMixin, SmartCreateView):
        success_message = ""

        def get(self, request, *args, **kwargs):
            return HttpResponseRedirect(reverse("reports.report_read"))

        def post(self, request, *args, **kwargs):
            json_string = request.body
            user = request.user
            org = user.get_org()

            try:
                json_dict = json.loads(json_string)
            except Exception as e:
                return JsonResponse(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            try:
                report = Report.create_report(org, user, json_dict)
            except Exception as e:  # pragma: needs cover
                traceback.print_exc(e)
                return JsonResponse(dict(status="error", description="Error creating report: %s" % str(e)), status=400)

            return JsonResponse(dict(status="success", description="Report Created", report=report.as_json()))

    class Analytics(OrgPermsMixin, SmartTemplateView):
        title = "Analytics"
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            org = self.request.user.get_org()

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
                    "stats": {"created_on": str(flow.created_on), "runs": FlowRunCount.get_totals(flow)},
                }

            flow_json = list(map(flow_cast, Flow.objects.filter(org_id=org.id, is_active=True)))

            groups = ContactGroup.user_groups.filter(org=org).order_by("name")
            groups_json = list(filter(lambda x: x is not None, [group.analytics_json() for group in groups]))

            reports = Report.objects.filter(is_active=True, org=org).order_by("title")
            reports_json = [report.as_json() for report in reports]

            current_report = None
            edit_report = self.request.GET.get("edit_report", None)
            if edit_report and int(edit_report):  # pragma: needs cover
                request_report = Report.objects.filter(pk=edit_report, org=org).first()
                if request_report:
                    current_report = json.dumps(request_report.as_json())

            state_fields = org.contactfields.filter(value_type=ContactField.TYPE_STATE)
            district_fields = org.contactfields.filter(value_type=ContactField.TYPE_DISTRICT)
            org_supports_map = org.country and state_fields.first() and district_fields.first()

            return dict(
                flows=flow_json,
                org_supports_map=org_supports_map,
                groups=groups_json,
                reports=reports_json,
                current_report=current_report,
            )

    class Results(OrgPermsMixin, SmartTemplateView):
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            # we must have the flow uuid to find the correct ruleset through the last revision
            flow_uuid = self.request.GET.get("flow_uuid", None)
            if not flow_uuid:
                return

            # we must have the ruleset uuid to search it on the last flow revision
            ruleset_uuid = self.request.GET.get("ruleset_uuid", None)
            if not ruleset_uuid:
                return

            # we must have the last revision and find the ruleset uuid in the definition
            last_flow_revision = (
                FlowRevision.objects.filter(flow__uuid=flow_uuid).only("definition").order_by("-id").first()
            )
            if not last_flow_revision:
                return

            node = dict()

            definition = last_flow_revision.definition
            for item in definition.get("nodes", []):
                if "router" not in item.keys():
                    continue

                if item.get("uuid") == ruleset_uuid:
                    ruleset_type = (
                        item.get("actions", [])[0]["type"]
                        if len(item.get("actions", [])) > 0
                        else None or item.get("router", {}).get("type")
                    )
                    ruleset_label = (
                        item.get("actions", [])[0]["result_name"]
                        if len(item.get("actions", [])) > 0
                        else None or item.get("router", {}).get("result_name")
                    )
                    node["uuid"] = ruleset_uuid
                    node["label"] = ruleset_label
                    node["type"] = ruleset_type
                    node["categories"] = item.get("router", {}).get("categories")
                    break

            filters = json.loads(self.request.GET.get("filters", "[]"))
            segment = json.loads(self.request.GET.get("segment", "null"))

            # todo: refactor accordingly to new architecture
            # results = Value.get_value_summary(ruleset=ruleset, filters=filters, segment=segment)
            results = {}
            return dict(uuid=node.get("uuid"), label=node.get("label"), results=results)

        def render_to_response(self, context, **response_kwargs):
            response = HttpResponse(json.dumps(context), content_type="application/json")
            return response

    class Choropleth(OrgPermsMixin, SmartReadView):
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            # todo: implement accordingly to the new architecture
            return dict(breaks={}, totals={}, scores={}, categories={})
