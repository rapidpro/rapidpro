from __future__ import unicode_literals


class OrgHeaderMiddleware(object):
    """
    Simple middleware to add a response header with the current org id, which can then be included in logs
    """
    def process_response(self, request, response):
        # if we have a user, log our org id
        if hasattr(request, 'user') and request.user.is_authenticated():
            org = request.user.get_org()
            if org:
                response['X-Temba-Org'] = org.id
        return response
