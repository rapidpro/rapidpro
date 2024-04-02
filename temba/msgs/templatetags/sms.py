from django import template
from django.utils.safestring import mark_safe

register = template.Library()

PLAYABLE_CONTENT_TYPES = {
    "audio/wav",
    "audio/x-wav",
    "audio/vnd.wav",
    "audio/ogg",
    "audio/mp3",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "video/mp4",
    "video/webm",
}


@register.tag(name="render")
def render(parser, token):
    """
    A block tag that renders its contents to a context variable.

    Here is an example of using it with a ``blocktrans`` tag::

        {% render as name %}
            <a href="{{ profile.get_absolute_url }}">{{ profile }}</a>
        {% endrender %}
        {% blocktrans %}Logged in as {{ name }}{% endblocktrans %}

    Here is an example of a simple base template that leverages this tag to
    avoid duplication of a page title::

        {% render as title %}
            {% block title %}The page title{% endblock %}
        {% endrender %}

        <html>
        <head><title>{{ title }}</title></head>
        <body>
            <h1>{{ title }}</h1>
            {% block body %}{% endblock %}
        </body>
    """

    class RenderNode(template.Node):
        def __init__(self, nodelist, as_var):
            self.nodelist = nodelist
            self.as_var = as_var

        def render(self, context):
            output = self.nodelist.render(context)
            context[self.as_var] = mark_safe(output.strip())
            return ""

    bits = token.split_contents()
    if len(bits) != 3 or bits[1] != "as":
        raise ValueError("render tag should be followed by keyword as and the name of a context variable")
    as_var = bits[2]

    nodes = parser.parse(("endrender",))
    parser.delete_first_token()
    return RenderNode(nodes, as_var)


@register.inclusion_tag("msgs/tags/attachment.html")
def attachment_button(attachment: str, show_thumb=False) -> dict:
    content_type, delim, url = attachment.partition(":")
    thumb = None

    # some OGG/OGA attachments may have wrong content type
    if content_type == "application/octet-stream" and (url.endswith(".ogg") or url.endswith(".oga")):
        content_type = "audio/ogg"

    # parse the MIME content type
    if "/" in content_type:
        category, sub_type = content_type.split("/", maxsplit=2)
    else:
        category, sub_type = content_type, ""

    if category == "image" and show_thumb:
        thumb = url

    if category == "geo":
        preview = url

        (lat, lng) = url.split(",")
        url = "http://www.openstreetmap.org/?mlat=%(lat)s&mlon=%(lng)s#map=18/%(lat)s/%(lng)s" % {
            "lat": lat,
            "lng": lng,
        }
    else:
        preview = (sub_type or category).upper()  # preview the sub type if it exists or category

    return {
        "content_type": content_type,
        "category": category,
        "preview": preview,
        "url": url,
        "is_playable": content_type in PLAYABLE_CONTENT_TYPES,
        "thumb": thumb,
    }
