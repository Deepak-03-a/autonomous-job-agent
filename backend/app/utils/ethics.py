"""Safety checks for the optional manual career-page fetcher."""

import asyncio
import ipaddress
import socket
import urllib.robotparser
from urllib.parse import urlsplit


class UnsafeTarget(ValueError):
    """The requested URL is not a publicly routable HTTP(S) destination."""


async def _resolve_public_target(url: str) -> tuple[str, str, str]:
    """Return normalized URL, hostname, and a vetted IP to pin the connection to."""
    try:
        parsed = urlsplit(url.strip())
        port = parsed.port
    except (AttributeError, ValueError) as error:
        raise UnsafeTarget("Enter a valid public HTTP or HTTPS URL.") from error
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        raise UnsafeTarget("Only public HTTP and HTTPS URLs are allowed.")
    if port not in (None, 80, 443):
        raise UnsafeTarget("Only the standard HTTP and HTTPS ports are allowed.")
    host = parsed.hostname.rstrip(".").casefold()
    if host in {"localhost", "localhost.localdomain", "metadata.google.internal"} or host.endswith(".local"):
        raise UnsafeTarget("Local and metadata service addresses are not allowed.")

    try:
        answers = await asyncio.to_thread(
            socket.getaddrinfo, host, port or (443 if parsed.scheme == "https" else 80),
            type=socket.SOCK_STREAM,
        )
    except OSError as error:
        raise UnsafeTarget("The destination hostname could not be resolved.") from error
    addresses = {answer[4][0].split("%", 1)[0] for answer in answers}
    if not addresses or any(not ipaddress.ip_address(address).is_global for address in addresses):
        raise UnsafeTarget("The destination resolves to a non-public IP address.")
    return url.strip(), host, sorted(addresses)[0]


async def validate_public_url(url: str) -> str:
    """Reject non-HTTP URLs and names resolving to loopback, private, or link-local IPs."""
    return (await _resolve_public_target(url))[0]


async def public_request(client, method: str, url: str, *, stream: bool = False):
    """Connect to the vetted address directly, preventing a DNS-rebinding SSRF."""
    normalized, host, address = await _resolve_public_target(url)
    import httpx

    original = httpx.URL(normalized)
    pinned = original.copy_with(host=address)
    headers = {"Host": original.netloc.decode("ascii")}
    extensions = {"sni_hostname": host.encode("idna").decode("ascii")} if original.scheme == "https" else {}
    request = client.build_request(method, pinned, headers=headers, extensions=extensions)
    return await client.send(request, stream=stream)


async def robots_allows(client, url: str) -> bool:
    """Fetch and honour robots.txt; deny on lookup errors and non-404 failures."""
    parsed = urlsplit(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    robots_url = f"{origin}/robots.txt"
    try:
        response = await public_request(client, "GET", robots_url)
    except Exception:
        return False
    if response.status_code == 404:
        return True
    if response.status_code != 200:
        return False
    parser = urllib.robotparser.RobotFileParser(robots_url)
    parser.parse(response.text.splitlines())
    return parser.can_fetch("TalentAlignAI", url)


class EthicalScrapingGuard:
    @staticmethod
    def check_robots_txt(url: str) -> bool:
        """Synchronous compatibility helper. Network errors fail closed."""
        try:
            parsed = urlsplit(url)
            validate_host = parsed.hostname or ""
            addresses = socket.getaddrinfo(validate_host, parsed.port or 443, type=socket.SOCK_STREAM)
            if any(not ipaddress.ip_address(item[4][0].split("%", 1)[0]).is_global for item in addresses):
                return False
            robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
            robot_parser = urllib.robotparser.RobotFileParser(robots_url)
            robot_parser.read()
            return robot_parser.can_fetch("TalentAlignAI", url)
        except Exception:
            return False
