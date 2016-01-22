from __future__ import absolute_import, unicode_literals

from rest_framework.renderers import BrowsableAPIRenderer


class DocumentationRenderer(BrowsableAPIRenderer):
    """
    The regular REST framework browsable API renderer includes a form on each endpoint. We don't provide that and
    instead have a separate API explorer page. This render then just displays the endpoint docs.
    """
    def get_context(self, data, accepted_media_type, renderer_context):
        view = renderer_context['view']
        request = renderer_context['request']
        response = renderer_context['response']
        renderer = self.get_default_renderer(view)

        return {
            'content': self.get_content(renderer, data, accepted_media_type, renderer_context),
            'view': view,
            'request': request,
            'response': response,
            'description': view.get_view_description(html=True),
            'name': self.get_name(view),
            'breadcrumblist': self.get_breadcrumbs(request),
        }
