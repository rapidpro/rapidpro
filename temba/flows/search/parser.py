import sqlparse

from django.db.models import Q, QuerySet
from django.utils.translation import ugettext_lazy as _
from sqlparse.exceptions import SQLParseError

from temba.utils.text import slugify_with


class FlowRunSearch(object):
    LOOKUPS = {
        "=": "__iexact",
        "~": "__icontains"
    }

    def __init__(self, query, base_queryset):
        self.query = self._preprocess_query(query)
        self.base_queryset = base_queryset

    def search(self) -> (QuerySet, str):
        from django.db.models.functions import Cast
        from django.contrib.postgres.fields import JSONField

        queryset = self.base_queryset.annotate(results_json=Cast('results', JSONField()))

        queries, e = self._parse_query()
        queries_iterator = iter(queries)

        filters = []
        previous_conditions = []
        while True:
            try:
                item = next(queries_iterator)
                if item.get("type") == "lookup":
                    _filter = Q(**{
                        f'results_json__{slugify_with(item.get("field"), "_")}'
                        f'__value{self.LOOKUPS.get(item.get("operator"))}':
                            item.get("value")
                    })
                    if previous_conditions:
                        previous_condition = previous_conditions.pop()
                        if previous_condition.get("conditional") == "NOT" and previous_conditions:
                            _filter = ~_filter
                            previous_condition = previous_conditions.pop()
                        if previous_condition.get("conditional") == "OR":
                            filters[-1] |= _filter
                        elif previous_condition.get("conditional") == "AND":
                            filters[-1] &= _filter
                        elif previous_condition.get("conditional") == "NOT":
                            filters.append(~_filter)
                    else:
                        filters.append(_filter)
                else:
                    previous_conditions.append(item)
            except StopIteration:
                break

        if filters:
            queryset = queryset.filter(*filters)

        return queryset, str(e) if e else ""

    @staticmethod
    def _preprocess_query(query: str) -> str:
        """
        This method adds spaces around operator and wrap keys and values into quotes
        so `sqlparse` will be able to parse it correctly.
        :param query: Query string received from client.
        :return: Processed query string.
        """
        query = (
            query
            .replace("!=", " <> ")
            .replace("=", " = ")
            .replace("~", " ~ ")
            .replace("(", " ( ")
            .replace(")", " ) ")
        )

        operators = ["=", "!=", "~", "<>", "not", "and", "or", "(", ")"]
        query_list = []
        need_quotes = set()
        previous_is_operator = False

        for item in query.split():
            if item.lower() in operators:
                query_list.append(item)
                previous_is_operator = True
            elif previous_is_operator or not query_list:
                query_list.append(item)
                previous_is_operator = False
                need_quotes.add(len(query_list) - 1)
            else:
                query_list[-1] = " ".join((query_list[-1], item))
                previous_is_operator = False
                need_quotes.add(len(query_list) - 1)

        for index in need_quotes:
            query_list[index] = f"'{query_list[index]}'"

        return " ".join(query_list)

    def _parse_query(self) -> (list, Exception):
        queries = []
        if "(" in self.query or ")" in self.query:
            return queries, Exception(_("Characters '(' and ')' are not allowed in results query."))
        try:
            parsed_query = sqlparse.parse(self.query)[0]
        except SQLParseError:
            return queries, Exception(_("Something is wrong with your query, please review the syntax."))
        for token in parsed_query.tokens:
            if token.is_keyword:
                queries.append({
                    "type": "conditional",
                    "conditional": str(token).upper()
                })
            else:
                match = str(token)
                if "=" in match:
                    match_splitted = str(match).split("=")
                    operator = "="
                elif "~" in match:
                    match_splitted = str(match).split("~")
                    operator = "~"
                elif "<>" in match:
                    match_splitted = str(match).split("<>")
                    operator = "="
                    if queries and queries[-1].get("conditional", "") == "NOT":
                        queries.pop()
                    else:
                        queries.append({
                            "type": "conditional",
                            "conditional": "NOT"
                        })
                else:
                    continue
                (field, value, *rest) = match_splitted
                queries.append({
                    "field": str(field).strip(" '"),
                    "value": str(value).strip(" '"),
                    "operator": operator,
                    "type": "lookup"
                })

        return queries, None
