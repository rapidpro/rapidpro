from unittest.mock import patch


class ESMockWithScroll:
    def __init__(self, data=None):
        self.mock_es = patch("temba.utils.es.ES")

        self.data = data if data is not None else []

    def __enter__(self):
        patched_object = self.mock_es.start()

        patched_object.search.return_value = {
            "_shards": {"failed": 0, "successful": 10, "total": 10},
            "timed_out": False,
            "took": 1,
            "_scroll_id": "1",
            "hits": {"hits": self.data},
        }
        patched_object.scroll.return_value = {
            "_shards": {"failed": 0, "successful": 10, "total": 10},
            "timed_out": False,
            "took": 1,
            "_scroll_id": "1",
            "hits": {"hits": []},
        }

        return patched_object()

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.mock_es.stop()


class ESMockWithScrollMultiple(ESMockWithScroll):
    def __enter__(self):
        patched_object = self.mock_es.start()

        patched_object.search.side_effect = [
            {
                "_shards": {"failed": 0, "successful": 10, "total": 10},
                "timed_out": False,
                "took": 1,
                "_scroll_id": "1",
                "hits": {"hits": return_value},
            }
            for return_value in self.data
        ]
        patched_object.scroll.side_effect = [
            {
                "_shards": {"failed": 0, "successful": 10, "total": 10},
                "timed_out": False,
                "took": 1,
                "_scroll_id": "1",
                "hits": {"hits": []},
            }
            for _ in range(len(self.data))
        ]

        return patched_object()
