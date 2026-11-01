"""robots.txt для кожного хоста (SPEC v2, 7.5). Читання файлу робить клієнт; тут лише правила."""
import urllib.robotparser
from urllib.parse import urlsplit


class RobotsGuard:
    def __init__(self, ua_token: str):
        self.ua = ua_token
        self._rules: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._status: dict[str, str] = {}   # ok | no_robots | unavailable

    def load(self, host: str, http_status: int | None, text: str | None) -> str:
        """200 -> правила; 404 -> усе дозволено; інше -> хост тимчасово недоступний."""
        rp = urllib.robotparser.RobotFileParser()
        if http_status == 200 and text is not None:
            rp.parse(text.splitlines())
            self._rules[host], self._status[host] = rp, "ok"
        elif http_status == 404:
            rp.parse([])
            self._rules[host], self._status[host] = rp, "no_robots"
        else:
            self._rules[host], self._status[host] = None, "unavailable"
        return self._status[host]

    def known(self, host: str) -> bool:
        return host in self._status

    def available(self, host: str) -> bool:
        return self._status.get(host) in ("ok", "no_robots")

    def forget(self, host: str) -> None:
        self._rules.pop(host, None)
        self._status.pop(host, None)

    def allowed(self, url: str) -> bool:
        host = urlsplit(url).hostname
        rp = self._rules.get(host)
        if rp is None:
            raise LookupError(f"robots.txt хоста {host} не завантажено")
        return rp.can_fetch(self.ua, url)
