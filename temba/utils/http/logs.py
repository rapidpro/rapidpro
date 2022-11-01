from datetime import datetime

from requests_toolbelt.utils import dump


class HttpLog:
    TRIM_URLS_TO = 2048
    TRIM_TRACES_TO = 50000

    def __init__(
        self,
        url: str,
        status_code: int,
        request: str,
        response: str,
        elapsed_ms: int,
        retries: int,
        created_on: datetime,
    ):
        self.url = url
        self.status_code = status_code
        self.request = request
        self.response = response
        self.elapsed_ms = elapsed_ms
        self.retries = retries
        self.created_on = created_on

    @classmethod
    def from_request(cls, request, created_on: datetime, ended_on: datetime):
        return cls._from_request_response(request, None, created_on, ended_on)

    @classmethod
    def from_response(cls, response, created_on: datetime, ended_on: datetime):
        return cls._from_request_response(response.request, response, created_on, ended_on)

    @classmethod
    def _from_request_response(cls, request, response, created_on: datetime, ended_on: datetime):
        prefixes = dump.PrefixSettings(b"", b"")
        proxy_info = dump._get_proxy_information(response) if response else None

        url = request.url[: cls.TRIM_URLS_TO]
        elapsed_ms = int((ended_on - created_on).total_seconds() * 1000)

        request_trace = bytearray()
        dump._dump_request_data(request, prefixes, request_trace, proxy_info=proxy_info)
        request_str = request_trace.decode("utf-8")[:-2]  # trim off extra \r\n

        if response:
            status_code = response.status_code
            response_trace = bytearray()
            dump._dump_response_data(response, prefixes, response_trace)
            response_str = response_trace.decode("utf-8")
        else:
            status_code = 0
            response_str = ""

        return cls(url, status_code, request_str, response_str, elapsed_ms, 0, created_on)
