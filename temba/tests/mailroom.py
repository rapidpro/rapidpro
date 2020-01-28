from unittest.mock import patch

from temba import mailroom


class MockMailroomParse:
    def __init__(self, result=None, error=None):
        self.patch = patch("temba.mailroom.client.MailroomClient")
        self.result = result
        self.error = error

        if result is None and error is None:  # pragma: no cover
            raise Exception("MockMailroomParse requires either a result or error")

    def __enter__(self):
        mock_mr = self.patch.__enter__()
        instance = mock_mr.return_value

        if self.result:
            instance.parse_query.return_value = self.result
        else:
            instance.side_effect = mailroom.MailroomException("", "", {"error": self.error})

        return mock_mr

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
