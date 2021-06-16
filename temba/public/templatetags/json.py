import json
from django import template


register = template.Library()
register.filter("json_dumps", json.dumps)
register.filter("json_loads", json.loads)
