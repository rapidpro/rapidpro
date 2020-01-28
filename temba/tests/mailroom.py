from unittest.mock import patch


class MockMailroomParse:
    def __init__(self, result=None):
        self.patch = patch("temba.mailroom.client.MailroomClient")
        self.result = result

    def __enter__(self):
        mock_mr = self.patch.__enter__()
        instance = mock_mr.return_value
        instance.parse_query.return_value = self.result
        return mock_mr

    def __exit__(self, exc_type, exc_val, exc_tb):
        return self.patch.__exit__(exc_type, exc_val, exc_tb)
