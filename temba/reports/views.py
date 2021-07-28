import json
import traceback

from django.shortcuts import reverse
from django.http import HttpResponseRedirect, JsonResponse, HttpResponse
from smartmin.views import SmartCRUDL, SmartCreateView, SmartTemplateView, SmartReadView
from temba.orgs.views import OrgPermsMixin
from .models import Report
from ..contacts.models import ContactField, ContactGroup
from ..flows.models import Flow, FlowRunCount


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

    class Results(OrgPermsMixin, SmartReadView):
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            filters = json.loads(self.request.GET.get('filters', '[]'))
            segment = json.loads(self.request.GET.get('segment', 'null'))

            ruleset = self.get_object()
            # todo: refactor accordingly to new architecture
            # results = Value.get_value_summary(ruleset=ruleset, filters=filters, segment=segment)
            results = {}
            return dict(id=ruleset.pk, label=ruleset.label, results=results)

        def render_to_response(self, context, **response_kwargs):
            response = HttpResponse(json.dumps(context), content_type='application/json')
            return response

    class Choropleth(OrgPermsMixin, SmartReadView):
        permission = "reports.report_read"

        def get_context_data(self, **kwargs):
            # todo: implement accordingly to the new architecture
            return dict(breaks={}, totals={}, scores={}, categories={})
