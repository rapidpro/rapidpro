from __future__ import unicode_literals

import json
import traceback

from django.core.urlresolvers import reverse
from django.http import HttpResponseRedirect
from smartmin.views import SmartCRUDL, SmartCreateView
from temba.utils import build_json_response
from temba.orgs.views import OrgPermsMixin
from .models import Report


class ReportCRUDL(SmartCRUDL):
    actions = ('create',)
    model = Report

    class Create(OrgPermsMixin, SmartCreateView):
        success_message = ''

        def get(self, request, *args, **kwargs):
            return HttpResponseRedirect(reverse('flows.ruleset_analytics'))

        def post(self, request, *args, **kwargs):
            json_string = request.body
            user = request.user
            org = user.get_org()

            try:
                json_dict = json.loads(json_string)
            except Exception as e:
                return build_json_response(dict(status="error", description="Error parsing JSON: %s" % str(e)), status=400)

            try:
                report = Report.create_report(org, user, json_dict)
            except Exception as e:
                traceback.print_exc(e)
                return build_json_response(dict(status="error", description="Error creating report: %s" % str(e)), status=400)

            return build_json_response(dict(status="success", description="Report Created", report=report.as_json()), status=200)
