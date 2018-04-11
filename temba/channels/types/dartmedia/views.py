# -*- coding: utf-8 -*-
from temba.channels.views import AuthenticatedExternalClaimView


class ClaimView(AuthenticatedExternalClaimView):

    def get_country(self, obj):
        return "Indonesia"

    def get_submitted_country(self, data):
        return "ID"
