import json
from urllib.error import HTTPError
from urllib.request import Request, urlopen


class ApiClient:
    def __init__(self, server: str, timeout: float = 30):
        base = server.rstrip("/")
        self.api_base = base if base.endswith("/api") else f"{base}/api"
        self.timeout = timeout

    def request(self, method: str, path: str, payload: dict | None = None) -> tuple[int, object | None]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {} if body is None else {"Content-Type": "application/json"}
        request = Request(f"{self.api_base}{path}", data=body, headers=headers, method=method)
        try:
            response = urlopen(request, timeout=self.timeout)
        except HTTPError as error:
            response = error
        with response:
            raw = response.read()
            if not raw:
                data = None
            else:
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    data = None
            return response.status, data
