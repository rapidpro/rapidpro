from unittest.mock import patch

from . import ParsedQuery, SearchException


class MockParseQuery:
    """
    Mocks temba.contacts.search.parse_query with the passed in query and fields
    """

    def __init__(self, query=None, fields=None, error=None):
        assert (query is not None and fields is not None and error is None) or (
            error is not None and query is None and fields is None
        )

        self.query = query
        self.fields = fields
        self.error = error

    def __enter__(self):
        self.patch = patch("temba.contacts.search.parse_query")
        mock = self.patch.__enter__()
        if self.error:
            mock.side_effect = SearchException(self.error)
        else:
            mock.return_value = ParsedQuery(query=self.query, fields=self.fields)

        return mock

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
