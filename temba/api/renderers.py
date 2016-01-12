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
            'description': self.get_description(view, renderer_context['response'].status_code),
            'name': self.get_name(view),
            'breadcrumblist': self.get_breadcrumbs(request),
        }

    def render(self, data, accepted_media_type=None, renderer_context=None):
        """
        Usually one customizes the browsable view by overriding the rest_framework/api.html template but we have two
        versions of the API to support with two different templates.
        """
        if not renderer_context:
            raise ValueError("Can't render without context")

        request_path = renderer_context['request'].path
        api_version = 1 if request_path.startswith('/api/v1') else 2

        self.template = 'api/v%d/api_root.html' % api_version

        return super(DocumentationRenderer, self).render(data, accepted_media_type, renderer_context)
