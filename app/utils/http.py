import requests
class PoliteSession(requests.Session):
    def __init__(self, user_agent: str | None = None):
        super().__init__()
        if user_agent:
            self.headers.update({"User-Agent": user_agent})
