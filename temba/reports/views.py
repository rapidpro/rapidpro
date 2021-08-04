import json
import traceback

import requests
from django.shortcuts import reverse
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from smartmin.views import SmartCRUDL, SmartCreateView, SmartTemplateView
from temba.orgs.views import OrgPermsMixin
from .models import Report
from .. import settings
from ..contacts.models import ContactGroup
from ..flows.models import Flow, FlowRunCount, FlowRevision


class ReportCRUDL(SmartCRUDL):
    actions = ("create", "analytics", "results")
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

            # get our list of assets to include
            scripts = []
            styles = []

            dev_mode = getattr(settings, "EDITOR_DEV_MODE", True)
            prefix = "http://localhost:3000" if dev_mode else settings.STATIC_URL

            # get our list of assets to include
            scripts = []
            styles = []

            if dev_mode:  # pragma: no cover
                response = requests.get("http://localhost:3000/asset-manifest.json")
                data = response.json()
            else:
                with open("node_modules/@greatnonprofits-nfp/temba-analytics/build/asset-manifest.json") as json_file:
                    data = json.load(json_file)

            def get_static_filename(filename):
                if dev_mode:
                    return f"{prefix}{filename}"
                return f"{settings.STATIC_URL}@greatnonprofits-nfp/temba-analytics/build{filename}"


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
                                # todo: add here contacts calculation for each category and rule.
                                "categories": [{"label": label, "contacts": 0} for label in rule["categories"]],
                                "created_on": str(flow.created_on),
                            },
                        }
                        for rule in flow.metadata.get("results", [])
                    ],
                    "stats": {"created_on": str(flow.created_on), "runs": sum(FlowRunCount.get_totals(flow).values())},
                }

            flow_json = list(map(flow_cast, Flow.objects.filter(org_id=org.id, is_active=True, is_archived=False)))

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

            return dict(
                analytics_context=json.dumps(dict(
                    flows=flow_json,
                    groups=groups_json,
                    reports=reports_json,
                    current_report=current_report,
                )),
                scripts=scripts,
                styles=styles,
            )

    class Results(OrgPermsMixin, SmartTemplateView):
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            org = self.get_user().get_org()

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

            ruleset = dict()

            definition = last_flow_revision.definition
            for item in definition.get("nodes", []):
                if "router" not in item.keys():
                    continue

                if item.get("uuid") == ruleset_uuid:
                    ruleset_type = item.get("router", {}).get("type")
                    ruleset_label = (
                        item.get("actions", [])[0]["result_name"]
                        if len(item.get("actions", [])) > 0
                        else None or item.get("router", {}).get("result_name")
                    )
                    ruleset["uuid"] = ruleset_uuid
                    ruleset["label"] = ruleset_label
                    ruleset["type"] = ruleset_type
                    ruleset["categories"] = item.get("router", {}).get("categories")
                    break

            filters = json.loads(self.request.GET.get("filters", "[]"))
            segment = json.loads(self.request.GET.get("segment", "null"))

            results = Report.get_value_summary(org=org, ruleset=ruleset, filters=filters, segment=segment)
            return dict(uuid=ruleset.get("uuid"), label=ruleset.get("label"), results=results)

        def render_to_response(self, context, **response_kwargs):
            response = HttpResponse(json.dumps(context), content_type="application/json")
            return response
