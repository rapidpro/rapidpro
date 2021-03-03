import re

from django.db.models import Q, QuerySet
from django.utils.translation import ugettext_lazy as _


class FlowRunSearch(object):
    LOOKUPS = {
        "=": "__iexact",
        "~": "__icontains"
    }

    def __init__(self, query, base_queryset):
        self.query = query
        self.base_queryset = base_queryset

    def search(self) -> (QuerySet, str):
        queryset = self.base_queryset

        # TODO Apply query
        queries, e = self._parse_query()
        query_set = dict()

        for item in queries:
            if item.get("type") == "lookup":
                print(item.get("field"), item.get("operator"), item.get("value"))
            else:
                print(item.get("conditional"))

        return queryset, str(e) if e else ""

    def _parse_query(self) -> (list, Exception):
        query_regex = r"\(([^)]+)\)"
        conditional_regex = r"(?:OR|AND|NOT)"

        query_matches = re.finditer(query_regex, self.query, re.IGNORECASE)
        query_matches_copy = re.finditer(query_regex, self.query, re.IGNORECASE)

        conditional_matches = re.finditer(conditional_regex, self.query, re.IGNORECASE)
        conditional_matches_copy = re.finditer(conditional_regex, self.query, re.IGNORECASE)

        query_matches_length = len([*query_matches_copy])
        conditional_matches_length = len([*conditional_matches_copy])

        if query_matches_length - conditional_matches_length != 1:
            return [], Exception(_("Something is wrong with your query, please review it."))

        conditional_items = []
        for item in conditional_matches:
            conditional_items.append(item.group())

        queries = []

        for idx, item in enumerate(query_matches):
            match = str(item.group()).replace("(", "").replace(")", "")
            if "=" in match:
                match_splitted = str(match).split("=")
                operator = "="
            elif "~" in match:
                match_splitted = str(match).split("~")
                operator = "~"
            else:
                continue

            (field, value, *rest) = match_splitted

            queries.append(
                dict(
                    field=str(field).strip(),
                    value=str(value).strip(),
                    operator=operator,
                    type="lookup"
                )
            )
            if idx < conditional_matches_length:
                queries.append(
                    dict(
                        type="conditional",
                        conditional=str(conditional_items[idx]).upper().strip(),
                    )
                )

        return queries, None
