from unittest.mock import patch

from . import ParsedQuery, SearchException


class MockParseQuery:
    """
    Mocks temba.contacts.search.parse_query with the passed in query and fields
    """

    def __init__(self, query=None, fields=None, elastic_query=None, allow_as_group=True, error=None):
        assert (query is not None and fields is not None and error is None) or (
            error is not None and query is None and fields is None
        )

        if not elastic_query:
            elastic_query = {"term": {"is_active": True}}

        self.query = query
        self.fields = fields
        self.elastic_query = elastic_query
        self.allow_as_group = allow_as_group
        self.error = error

    def __enter__(self):
        self.patch = patch("temba.contacts.search.parse_query")
        mock = self.patch.__enter__()
        if self.error:
            mock.side_effect = SearchException(self.error)
        else:
            mock.return_value = ParsedQuery(
                query=self.query,
                fields=self.fields,
                elastic_query=self.elastic_query,
                allow_as_group=self.allow_as_group,
            )

        return mock

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
