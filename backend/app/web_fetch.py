"""Bounded, cancellation-aware HTTP fetching with DNS-pinned connections.

No cookies, credentials, environment proxies, browser execution, or automatic redirects.
All redirect targets are independently resolved, checked, and connected by numeric IP.
"""
from __future__ import annotations

import hashlib
import http.client
import ipaddress
import queue
import re
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import quote, urljoin, urlsplit, urlunsplit

from app.config import get_web_fetch_config

MAX_BYTES = 2 * 1024 * 1024
MAX_CHARS = 20_000
FOCUSED_CHARS = 6_000
MAX_REDIRECTS = 5
MAX_SECONDS = 30
MAX_URL_CHARS = 2048
# Match Collomia's fixed desktop Chrome identity for public web requests.
# Review the browser version when preparing a release.
USER_AGENT = (
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) '
    'AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150.0.0.0 Safari/537.36'
)
_DNS_SLOTS = threading.BoundedSemaphore(4)


class FetchError(ValueError):
    def __init__(self, status, message, http_status=None):
        super().__init__(message)
        self.status, self.http_status = status, http_status


def normalize_url(url):
    if not isinstance(url, str) or len(url) > MAX_URL_CHARS or any(ord(c) < 33 for c in url) or '\\' in url:
        raise FetchError('invalid_url', 'Invalid or oversized URL.')
    try:
        parts = urlsplit(url)
        if parts.scheme not in {'http', 'https'} or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError()
        host = parts.hostname.encode('idna').decode('ascii').lower()
        if '%' in host:
            raise ValueError()
        port = parts.port if parts.port is not None else (443 if parts.scheme == 'https' else 80)
        if not 1 <= port <= 65535:
            raise ValueError()
        authority = f'[{host}]' if ':' in host else host
        if port != (443 if parts.scheme == 'https' else 80):
            authority += f':{port}'
        result = urlunsplit((parts.scheme, authority, quote(parts.path or '/', safe="/%:@!$&'()*+,;=-._~"),
                            quote(parts.query, safe="/%?:@!$&'()*+,;=-._~"), ''))
        if len(result) > MAX_URL_CHARS:
            raise ValueError()
        return result
    except (ValueError, UnicodeError):
        raise FetchError('invalid_url', 'Use an HTTP(S) URL without embedded credentials.') from None


def host_allowlisted(host, allowlist):
    for raw in allowlist:
        entry = str(raw or '').strip()
        if entry.lower() == host.lower():
            return True
        try:
            if ipaddress.ip_address(host) in ipaddress.ip_network(entry, strict=False):
                return True
        except ValueError:
            pass
    return False


def blocked_ip(value):
    try:
        address = ipaddress.ip_address(value)
        if isinstance(address, ipaddress.IPv6Address):
            # Deprecated site-local and transition/translation ranges can reach IPv4
            # destinations that are not apparent from a public-looking IPv6 address.
            if address.is_site_local or address.sixtofour or address.teredo or any(
                address in ipaddress.ip_network(network) for network in ('64:ff9b::/96', '64:ff9b:1::/48')
            ):
                return True
        return not address.is_global or address.is_multicast
    except ValueError:
        return True


class Deadline:
    def __init__(self, cancel=None):
        self.cancel, self.until = cancel, time.monotonic() + MAX_SECONDS
        self.finished = threading.Event()
        self.lock = threading.Lock()
        self.sock = None

    def check(self):
        if self.cancel is not None and self.cancel.is_set():
            raise FetchError('cancelled', 'Fetch stopped. No new evidence was captured.')
        if time.monotonic() >= self.until:
            raise FetchError('timeout', 'Fetch exceeded the time limit.')

    def attach(self, sock):
        with self.lock:
            self.sock = sock
        self.check()

    def watch(self):
        while not self.finished.wait(0.05):
            try:
                self.check()
            except FetchError:
                with self.lock:
                    if self.sock:
                        try:
                            self.sock.shutdown(socket.SHUT_RDWR)
                        except OSError:
                            pass
                return

    def __enter__(self):
        self.thread = threading.Thread(target=self.watch, daemon=True, name='phlox-fetch-deadline')
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.finished.set()
        self.thread.join()


def resolve(host, port, deadline):
    """Bound resolver concurrency; a stuck OS resolver cannot block Stop or grow a queue."""
    deadline.check()
    if not _DNS_SLOTS.acquire(blocking=False):
        raise FetchError('dns_busy', 'DNS resolver is busy. Retry later.')
    result = queue.Queue(maxsize=1)
    def work():
        try:
            result.put(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            result.put(None)
        finally:
            _DNS_SLOTS.release()
    threading.Thread(target=work, daemon=True, name='phlox-fetch-dns').start()
    while True:
        deadline.check()
        try:
            addresses = result.get(timeout=0.05)
            if not addresses:
                raise FetchError('dns_error', 'Could not resolve the requested host.')
            return addresses
        except queue.Empty:
            continue


def checked_addresses(url, deadline, config=None):
    parts = urlsplit(normalize_url(url))
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    addresses = resolve(parts.hostname, port, deadline)
    cfg = get_web_fetch_config() if config is None else config
    allowed = cfg.get('allow_private_networks') or host_allowlisted(parts.hostname, cfg.get('allowlist_hosts') or [])
    if not allowed and any(blocked_ip(info[4][0]) for info in addresses):
        raise FetchError('blocked', 'Fetch blocked: target resolves to a private or non-public address.')
    return addresses


def connection(url, addresses, deadline):
    """Connect a numeric sockaddr once; HTTP must never reconnect through hostname DNS."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == 'https' else 80)
    last = None
    for family, kind, protocol, _, address in addresses[:8]:
        deadline.check()
        sock = socket.socket(family, kind, protocol)
        try:
            sock.settimeout(3)
            deadline.attach(sock)
            sock.connect(address)
            if ipaddress.ip_address(sock.getpeername()[0]) != ipaddress.ip_address(address[0]):
                raise FetchError('blocked', 'Connected peer did not match the validated address.')
            if parts.scheme == 'https':
                sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname,
                                                               do_handshake_on_connect=False)
                deadline.attach(sock)
                sock.do_handshake()
            conn = http.client.HTTPConnection(parts.hostname, port=port, timeout=3)
            conn.auto_open = 0  # no reconnect (and hence no second hostname resolution)
            conn.sock = sock
            return conn
        except (OSError, FetchError) as exc:
            sock.close()
            last = exc
    deadline.check()
    raise FetchError('connection_error', 'Could not establish a verified connection to the page.') from last


class PageParser(HTMLParser):
    """Extract readable text and headings while dropping executable/hidden page content."""
    SKIP = {'script', 'style', 'noscript', 'template', 'svg', 'nav'}
    BLOCK = {'p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'section', 'article'}
    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.parts, self.title = [], [], []
        self.paywall = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        hidden = tag in self.SKIP or 'hidden' in attrs or attrs.get('aria-hidden') == 'true'
        hidden |= (attrs.get('role') or '').lower() in {'navigation', 'banner', 'contentinfo'}
        # Keep article headers/footers (headings, bylines, references), but omit site chrome.
        hidden |= tag in {'header', 'footer'} and not any(t in {'article', 'main'} for t, _ in self.stack)
        if tag not in self.VOID:
            self.stack.append((tag, hidden))
            if len(self.stack) > 128:
                raise FetchError('invalid_html', 'Page nesting exceeds the extraction limit.')
        if tag == 'meta' and (attrs.get('itemprop') or '').lower() == 'isaccessibleforfree' and attrs.get('content') == 'false':
            self.paywall = True
        if tag in self.BLOCK:
            self.parts.append('\n')
        elif tag in {'td', 'th'}:
            self.parts.append(' | ')

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, -1, -1):
            if self.stack[index][0] == tag:
                del self.stack[index:]
                break
        if tag in self.BLOCK:
            self.parts.append('\n')

    def handle_data(self, data):
        if any(hidden for _, hidden in self.stack):
            return
        if any(tag == 'title' for tag, _ in self.stack):
            self.title.append(data)
        elif not any(tag == 'head' for tag, _ in self.stack):
            self.parts.append(data)

    def text(self):
        return '\n'.join(line for raw in ''.join(self.parts).splitlines() if (line := re.sub(r'\s+', ' ', raw).strip()))


@dataclass
class Page:
    url: str
    title: str
    text: str
    content_hash: str
    truncated: bool
    http_status: int
    start_char: int = 0
    total_chars: int | None = None
    passages: list | None = None
    notice: str = ''


def validate_selection(query, start_char, max_chars):
    if not isinstance(query, str) or len(query) > 200:
        raise FetchError('invalid_selection', 'query must be text of at most 200 characters.')
    if type(start_char) is not int or not 0 <= start_char <= MAX_BYTES:
        raise FetchError('invalid_selection', 'start_char must be a nonnegative character offset within the download bound.')
    if type(max_chars) is not int or not 1 <= max_chars <= MAX_CHARS:
        raise FetchError('invalid_selection', f'max_chars must be between 1 and {MAX_CHARS}.')


def select_passage(text, query, start_char, max_chars, deadline):
    """Rank bounded, contiguous windows; return original text and absolute offsets.

    Lexical matching is a navigation aid, not semantic evidence verification. Never join
    disjoint matches into a passage or change offsets through case folding/normalization.
    """
    if start_char >= len(text):
        raise FetchError('selection_empty', f'No text at start_char={start_char}; extracted page has {len(text)} characters.')
    if query.strip():
        terms = list(dict.fromkeys(re.findall(r'\w+', query.lower())))[:20]
        if not terms:
            raise FetchError('invalid_selection', 'query must contain a word or number.')
        patterns = [re.compile(r'(?<!\w)' + re.escape(term) + r'(?!\w)', re.I) for term in terms]
        width = min(max_chars, FOCUSED_CHARS)
        best, best_score = start_char, (0, 0)
        # Half-window overlap reduces the chance of separating a heading from its table.
        for offset in range(start_char, len(text), max(1, width // 2)):
            deadline.check()
            window = text[offset:offset + width]
            score = (int(query.lower() in window.lower()), sum(bool(p.search(window)) for p in patterns))
            if score > best_score:
                best, best_score = offset, score
        if best_score == (0, 0):
            raise FetchError('selection_empty', 'No matching passage found in extracted text. Try different keywords or use start_char pagination.')
        start_char, max_chars = best, width
    return text[start_char:start_char + max_chars], start_char


def fetch(url, cancel=None, url_policy=None, *, query='', start_char=0, max_chars=MAX_CHARS,
          pdf_page=None, json_pointer='', json_start=0, json_limit=20):
    from app import web_formats
    validate_selection(query, start_char, max_chars)
    web_formats.validate(pdf_page, json_pointer, json_start, json_limit)
    current = normalize_url(url)
    with Deadline(cancel) as deadline:
        try:
            for hop in range(MAX_REDIRECTS + 1):
                deadline.check()
                if url_policy is not None and not url_policy(current):
                    raise FetchError('scope_blocked', 'URL or redirect is outside the selected research domains.')
                conn = connection(current, checked_addresses(current, deadline), deadline)
                try:
                    parts = urlsplit(current)
                    conn.request('GET', parts.path + ('?' + parts.query if parts.query else ''),
                                 headers={'User-Agent': USER_AGENT, 'Accept-Encoding': 'identity',
                                          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,'
                                                    'text/plain;q=0.8,application/json;q=0.8,*/*;q=0.5',
                                          'Accept-Language': 'en-US,en;q=0.9', 'Connection': 'close'})
                    resp = conn.getresponse()
                    if resp.status in {301, 302, 303, 307, 308}:
                        if not resp.getheader('Location') or hop == MAX_REDIRECTS:
                            raise FetchError('redirect_error', 'Missing redirect target or too many redirects.', resp.status)
                        current = normalize_url(urljoin(current, resp.getheader('Location')))
                        continue
                    if not 200 <= resp.status < 300:
                        label = 'Access denied or payment/login required.' if resp.status in {401, 402, 403} else 'Page request failed.'
                        raise FetchError('http_error', f'HTTP {resp.status}: {label} No page evidence captured.', resp.status)
                    ctype = resp.headers.get_content_type()
                    format = 'pdf' if ctype == 'application/pdf' else 'json' if (
                        ctype == 'application/json' or (ctype.startswith('application/') and ctype.endswith('+json'))) else None
                    if not format and ctype not in {'text/html', 'text/plain', 'text/markdown', 'application/xhtml+xml'}:
                        raise FetchError('unsupported_type', 'Unsupported source type. Use HTML, text, PDF or JSON.', resp.status)
                    body = read_body(resp, deadline)
                    if format:
                        return web_formats.page(bytes(body), format, current, resp.status, deadline,
                            query=query, start_char=start_char, max_chars=max_chars, pdf_page=pdf_page,
                            json_pointer=json_pointer, json_start=json_start, json_limit=json_limit)
                    if pdf_page is not None or json_pointer or json_start or json_limit != 20:
                        raise FetchError('invalid_selection', 'PDF/JSON selectors do not apply to this HTML/text response.')
                    try:
                        text = body.decode(resp.headers.get_content_charset() or 'utf-8', errors='replace')
                    except LookupError:
                        raise FetchError('unsupported_encoding', 'Unknown page character encoding.', resp.status) from None
                    title, paywall = urlsplit(current).hostname, False
                    if ctype in {'text/html', 'application/xhtml+xml'}:
                        parser = PageParser()
                        parser.feed(text)
                        text, title, paywall = parser.text(), ''.join(parser.title).strip() or title, parser.paywall
                    text = text.strip()
                    if paywall or (len(text) < 1500 and re.search(r'subscribe to (?:continue|read)|sign in to (?:continue|read)|verify you are human|enable javascript and cookies', text, re.I)):
                        raise FetchError('access_limited', 'Possible paywall, login, or challenge page. No article evidence captured.', resp.status)
                    if not text or '\x00' in text:
                        raise FetchError('empty', 'No readable page text. Browser-rendered or binary pages are unsupported.', resp.status)
                    deadline.check()
                    excerpt, start = select_passage(text, query, start_char, max_chars, deadline)
                    deadline.check()
                    return Page(current, re.sub(r'\s+', ' ', title)[:500], excerpt,
                                hashlib.sha256(text.encode()).hexdigest(), len(excerpt) < len(text), resp.status,
                                start, len(text))
                finally:
                    conn.close()
        except (OSError, http.client.HTTPException):
            deadline.check()
            raise FetchError('connection_error', 'Fetch connection failed or timed out. No page evidence captured.') from None


def read_body(resp, deadline):
    """Shared download bounds for web pages and fixed-endpoint read-query adapters."""
    if resp.getheader('Content-Encoding', 'identity').lower() != 'identity':
        raise FetchError('unsupported_encoding', 'Server returned compressed content despite an identity request.', resp.status)
    length = resp.getheader('Content-Length')
    if length and (len(length) > 10 or not length.isdigit() or int(length) > MAX_BYTES):
        raise FetchError('too_large', 'Page exceeds the 2 MiB download limit.', resp.status)
    body = bytearray()
    while True:
        deadline.check()
        block = resp.read1(min(65536, MAX_BYTES + 1 - len(body)))
        if not block:
            break
        body.extend(block)
        if len(body) > MAX_BYTES:
            raise FetchError('too_large', 'Page exceeds the 2 MiB download limit.', resp.status)
    deadline.check()
    if length and len(body) != int(length):
        raise FetchError('incomplete', 'Page download ended before its declared length. No evidence captured.', resp.status)
    return bytes(body)


def post_read_query(url, body, deadline, url_policy=None):
    return read_api_query(url, deadline, url_policy, body=body)


def read_api_query(url, deadline, url_policy=None, *, body=None, response_format='json'):
    """Transport for trusted read adapters, never exposed as an arbitrary POST tool.

    The adapter owns the endpoint and request schema. Redirects are rejected rather than
    forwarding a query to a different path/host or changing the method. No retries.
    """
    current = normalize_url(url)
    if body is not None and len(body) > 8192:
        raise FetchError('invalid_selection', 'Read query exceeds the 8 KiB request limit.')
    deadline.check()
    if url_policy is not None and not url_policy(current):
        raise FetchError('scope_blocked', 'API is outside the selected research domains.')
    conn = connection(current, checked_addresses(current, deadline), deadline)
    try:
        parts = urlsplit(current)
        conn.request('POST' if body is not None else 'GET', parts.path + ('?' + parts.query if parts.query else ''), body=body,
                     headers={'User-Agent': USER_AGENT, 'Accept': 'application/xml, text/xml' if response_format == 'xml' else 'application/json',
                              'Accept-Encoding': 'identity', 'Content-Type': 'application/json',
                              'Connection': 'close'})
        resp = conn.getresponse()
        if 300 <= resp.status < 400:
            raise FetchError('redirect_error', 'Read-query redirects are not followed. No evidence captured.', resp.status)
        if not 200 <= resp.status < 300:
            raise FetchError('http_error', f'HTTP {resp.status}: API query failed. No evidence captured.', resp.status)
        ctype = resp.headers.get_content_type()
        supported = (ctype in {'application/xml', 'text/xml'} if response_format == 'xml' else
                     ctype == 'application/json' or (ctype.startswith('application/') and ctype.endswith('+json')))
        if not supported:
            raise FetchError('unsupported_type', f'Read-query API did not return {response_format.upper()}.', resp.status)
        return read_body(resp, deadline), resp.status
    except (OSError, http.client.HTTPException):
        deadline.check()
        raise FetchError('connection_error', 'API connection failed or timed out. No evidence captured.') from None
    finally:
        conn.close()
