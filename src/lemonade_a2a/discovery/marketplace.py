"""Find remote A2A agents from an agent marketplace given only its link or domain name.

A marketplace (also called an agent registry or catalog) is any HTTPS endpoint that lists
agents. There is no single standard listing format yet, so this module accepts the common
shapes and normalizes them to validated A2A Agent Cards:

* a bare domain (``agents.example.com``): the well-known listing paths are probed in order
  (``LISTING_PATHS``); a domain that only serves one Agent Card is a marketplace of one;
* a full URL (``https://agents.example.com/api/agents``): fetched as is;
* the document may be a JSON array, or an object holding the array under ``agents``,
  ``items``, ``results``, ``data``, ``entries``, ``cards`` or ``records``;
* each entry may be a full Agent Card (A2A 1.x ``supportedInterfaces`` or 0.3 ``url``), an
  object that embeds one (``agentCard``/``card``), an object that points at one
  (``agentCardUrl``/``cardUrl``/``wellKnownURI``...), an object with the agent's base URL
  (``url``/``baseUrl``/``endpoint``; the card is read from its well-known path), or a plain
  URL string. Relative URLs are resolved against the listing.

Everything a marketplace returns is untrusted: every request (listing, cards, and the A2A
calls that follow) goes through the SSRF-guarded transport, redirects are refused, sizes and
counts are bounded, and each card's advertised endpoints must pass the same URL policy.
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import re
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx

from lemonade_a2a.client._ssrf_transport import SSRFGuardedTransport
from lemonade_a2a.client.errors import AgentDiscoveryError, OutboundA2AError
from lemonade_a2a.discovery import AgentLocation, validate_remote_url
from lemonade_a2a.discovery.cards import MAX_CARD_BYTES, validate_agent_card
from lemonade_a2a.discovery.dns import AGENT_SERVICE, REGISTRY_SERVICE, Resolver, lookup_service

LOG = logging.getLogger("lemonade_a2a.marketplace")

CARD_PATH = "/.well-known/agent-card.json"
LISTING_PATHS = (
    "/.well-known/agent-registry.json",
    "/.well-known/agents.json",
    "/agents.json",
    CARD_PATH,
)
LIST_KEYS = ("agents", "items", "results", "data", "entries", "agentCards", "cards", "records")
CARD_OBJECT_KEYS = ("agentCard", "agent_card", "card")
CARD_URL_KEYS = (
    "agentCardUrl",
    "agentCardURL",
    "agent_card_url",
    "cardUrl",
    "card_url",
    "wellKnownURI",
    "well_known_uri",
)
BASE_URL_KEYS = ("url", "baseUrl", "base_url", "endpoint")
ID_KEYS = ("id", "agentId", "agent_id", "slug")
CARD_HINT_KEYS = (
    "skills",
    "capabilities",
    "protocolVersion",
    "defaultInputModes",
    "preferredTransport",
)

MAX_LISTING_BYTES = 4 * 1024 * 1024
MAX_AGENTS = 200
CARD_FETCH_CONCURRENCY = 8
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")


class MarketplaceError(AgentDiscoveryError):
    """A marketplace could not be read or did not list agents in a recognised shape."""


def _clean(value: Any, limit: int) -> str:
    """Single-line, bounded text from an untrusted field (it may end up in a prompt)."""
    if not isinstance(value, str):
        return ""
    text = " ".join(_CONTROL.sub(" ", value).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:64] or "agent"


@dataclass(frozen=True)
class MarketplaceAgent:
    """One remote agent listed by a marketplace, with its validated Agent Card."""

    id: str
    name: str
    description: str
    card_url: str
    source: str
    card: dict[str, Any] = field(repr=False, compare=False)

    @property
    def endpoint_url(self) -> str:
        interfaces = self.card.get("supportedInterfaces") or []
        if interfaces:
            return interfaces[0]["url"]
        return self.card["url"]

    @property
    def skills(self) -> list[dict[str, Any]]:
        skills = self.card.get("skills")
        if not isinstance(skills, list):
            return []
        return [
            {
                "id": _clean(skill.get("id"), 64),
                "name": _clean(skill.get("name"), 80),
                "description": _clean(skill.get("description"), 160),
                "tags": [
                    _clean(tag, 32) for tag in skill.get("tags") or [] if isinstance(tag, str)
                ][:8],
            }
            for skill in skills[:16]
            if isinstance(skill, dict)
        ]

    def location(self) -> AgentLocation:
        parts = urlsplit(self.card_url)
        return AgentLocation(
            base_url=urlunsplit((parts.scheme, parts.netloc, "", "", "")),
            card_path=parts.path or CARD_PATH,
        )

    def summary(self) -> str:
        """A compact, sanitized description for humans and for routing prompts."""
        skills = "; ".join(
            f"{s['name'] or s['id']}: {s['description']}".strip(": ") for s in self.skills[:8]
        )
        text = f"{self.id} - {self.name}: {self.description}"
        return f"{text} [skills: {skills}]" if skills else text

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "card_url": self.card_url,
            "endpoint": self.endpoint_url,
            "source": self.source,
            "skills": self.skills,
        }


def _local_host(host: str) -> bool:
    if host == "localhost" or host.endswith((".local", ".localhost")):
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


DNS_PREFIX = "dns:"
_DOMAIN = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")


def srv_domain(source: str) -> str | None:
    """The domain to look up SRV records for: ``dns:<domain>`` or a bare domain name."""
    text = source.strip().lower() if isinstance(source, str) else ""
    if text.startswith(DNS_PREFIX):
        text = text[len(DNS_PREFIX) :].strip().rstrip(".")
        if not _DOMAIN.match(text):
            raise MarketplaceError(f"{source!r} is not a DNS domain name")
        return text
    if "://" in text or "/" in text or ":" in text:
        return None  # a link, or host:port (no SRV for an explicit port)
    text = text.rstrip(".")
    return text if _DOMAIN.match(text) else None


def listing_candidates(source: str, *, allow_local: bool = False) -> list[str]:
    """URLs to try for a marketplace given as a link or a bare domain name.

    ``dns:<domain>`` is found through DNS SRV records only, so it has no fixed URLs here.
    """
    text = source.strip() if isinstance(source, str) else ""
    if not text:
        raise MarketplaceError("marketplace link or domain name is empty")
    if text.lower().startswith(DNS_PREFIX):
        srv_domain(text)
        return []
    if "://" not in text:
        host = urlsplit(f"//{text}").hostname or ""
        scheme = "http" if allow_local and _local_host(host.lower()) else "https"
        text = f"{scheme}://{text}"
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https"):
        raise MarketplaceError("marketplace URL must use https")
    origin = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    try:
        validate_remote_url(origin, allow_local=allow_local)
    except ValueError as exc:
        raise MarketplaceError(f"marketplace URL is not allowed: {exc}") from exc
    if parts.path in ("", "/") and not parts.query:
        return [origin + path for path in LISTING_PATHS]
    return [urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))]


def looks_like_card(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("name"), str)
        and (
            isinstance(value.get("supportedInterfaces"), list)
            or (isinstance(value.get("url"), str) and any(k in value for k in CARD_HINT_KEYS))
        )
    )


def listing_entries(document: Any, *, depth: int = 0) -> list[Any]:
    """The list of agent entries in a marketplace document."""
    if isinstance(document, list):
        return document
    if isinstance(document, dict):
        if looks_like_card(document):
            return [document]
        for key in LIST_KEYS:
            value = document.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict) and depth == 0:
                return listing_entries(value, depth=1)
    raise MarketplaceError("marketplace response is not a recognised agent listing")


@dataclass(frozen=True)
class _Entry:
    hint_id: str
    card: dict[str, Any] | None
    card_url: str


def parse_entry(entry: Any, listing_url: str) -> _Entry:
    """Normalize one listing entry to an embedded card or the URL of its card."""

    def card_url_from_base(value: str) -> str:
        url = urljoin(listing_url, value.strip())
        return url if urlsplit(url).path.endswith(".json") else url.rstrip("/") + CARD_PATH

    if isinstance(entry, str):
        return _Entry("", None, card_url_from_base(entry))
    if not isinstance(entry, dict):
        raise MarketplaceError("listing entry is neither an object nor a URL")
    hint = next((entry[k] for k in ID_KEYS if isinstance(entry.get(k), str | int)), "")
    hint_id = str(hint)
    if looks_like_card(entry):
        return _Entry(hint_id, entry, listing_url)
    for key in CARD_OBJECT_KEYS:
        if looks_like_card(entry.get(key)):
            return _Entry(hint_id, entry[key], listing_url)
    for key in CARD_URL_KEYS:
        if isinstance(entry.get(key), str) and entry[key].strip():
            return _Entry(hint_id, None, urljoin(listing_url, entry[key].strip()))
    for key in BASE_URL_KEYS:
        if isinstance(entry.get(key), str) and entry[key].strip():
            return _Entry(hint_id, None, card_url_from_base(entry[key]))
    raise MarketplaceError("listing entry has no Agent Card or card URL")


class Marketplace:
    """Agents listed by one or more marketplaces, refreshed every ``refresh_seconds``.

    ``sources`` are links or bare domain names. ``api_key`` is sent as a bearer token to
    the marketplace hosts only (never to the agents they list). ``allowed_hosts``, when
    given, restricts every request (listing, cards and A2A calls) to those hosts.
    ``allow_local`` permits ``http`` and private addresses for a development or LAN
    marketplace; leave it off for anything internet-facing.

    Implements the discovery ``resolve(alias)`` interface, so it can stand in for
    :class:`~lemonade_a2a.discovery.StaticDiscovery`.
    """

    def __init__(
        self,
        sources: str | Sequence[str],
        *,
        allow_local: bool = False,
        allowed_hosts: Iterable[str] | None = None,
        api_key: str = "",
        timeout: float = 20.0,
        refresh_seconds: float = 300.0,
        max_agents: int = MAX_AGENTS,
        dns_resolver: Resolver | None = None,
    ) -> None:
        if isinstance(sources, str):
            sources = [part for part in (s.strip() for s in sources.split(",")) if part]
        self.sources = list(sources)
        if not self.sources:
            raise MarketplaceError("at least one marketplace link or domain name is required")
        self.allow_local = allow_local
        self.allowed_hosts = (
            frozenset(host.lower() for host in allowed_hosts) if allowed_hosts else None
        )
        self.timeout = timeout
        self.refresh_seconds = refresh_seconds
        self.max_agents = max_agents
        self._api_key = api_key
        self._candidates = {
            source: listing_candidates(source, allow_local=allow_local) for source in self.sources
        }
        self._srv_domains = {source: srv_domain(source) for source in self.sources}
        # The key goes to the hosts the operator named, not to hosts that DNS answers name:
        # unsigned DNS can be spoofed.
        self._marketplace_hosts = frozenset(
            (urlsplit(urls[0]).hostname or "").lower() if urls else self._srv_domains[source]
            for source, urls in self._candidates.items()
        )
        self._dns_resolver = dns_resolver
        self._agents: list[MarketplaceAgent] | None = None
        self._loaded_at = 0.0
        self._lock = asyncio.Lock()
        self.problems: list[str] = []

    def __repr__(self) -> str:  # never shows the api key
        return f"Marketplace({self.sources!r}, allow_local={self.allow_local})"

    # -- HTTP -----------------------------------------------------------------------------

    def _http_client(self) -> httpx.AsyncClient:
        transport = SSRFGuardedTransport(
            allow_local=self.allow_local, allowed_hosts=self.allowed_hosts
        )
        return httpx.AsyncClient(
            transport=transport, timeout=self.timeout, follow_redirects=False, trust_env=False
        )

    async def _get_json(self, http: httpx.AsyncClient, url: str, limit: int) -> Any | None:
        """The decoded JSON at ``url``; ``None`` when the server says it is not there."""
        headers = {"Accept": "application/json"}
        if self._api_key and (urlsplit(url).hostname or "").lower() in self._marketplace_hosts:
            headers["Authorization"] = f"Bearer {self._api_key}"
        async with http.stream("GET", url, headers=headers) as response:
            if response.is_redirect:
                raise MarketplaceError(f"redirects are forbidden ({url})")
            if response.status_code in (404, 410):
                return None
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body += chunk
                if len(body) > limit:
                    raise MarketplaceError(f"response exceeds {limit} bytes ({url})")
        try:
            return json.loads(body)
        except ValueError as exc:
            raise MarketplaceError(f"response is not JSON ({url})") from exc

    # -- loading --------------------------------------------------------------------------

    async def _srv_candidates(self, domain: str, problems: list[str]) -> list[str]:
        """Listing (and single-card) URLs that the domain's SRV records point to."""
        urls: list[str] = []
        lookups = ((REGISTRY_SERVICE, LISTING_PATHS), (AGENT_SERVICE, (CARD_PATH,)))
        for service, default_paths in lookups:
            for target in await lookup_service(domain, service, self._dns_resolver):
                local = self.allow_local and _local_host(target.host)
                origin = target.origin("http" if local else "https")
                try:
                    validate_remote_url(origin, allow_local=self.allow_local)
                except ValueError as exc:
                    problems.append(f"SRV {service}.{domain} -> {target.host}: {exc}")
                    continue
                urls += [
                    origin + path for path in ((target.path,) if target.path else default_paths)
                ]
        return urls

    async def _read_listing(
        self, http: httpx.AsyncClient, source: str, problems: list[str]
    ) -> tuple[str, list[Any]]:
        last_error: Exception | None = None
        candidates = list(self._candidates[source])
        domain = self._srv_domains[source]
        if domain:
            candidates = [*await self._srv_candidates(domain, problems), *candidates]
        for url in candidates:
            try:
                document = await self._get_json(http, url, MAX_LISTING_BYTES)
            except (MarketplaceError, httpx.HTTPError, ValueError) as exc:
                last_error = exc
                continue
            if document is None:
                continue
            try:
                return url, listing_entries(document)
            except MarketplaceError as exc:
                last_error = exc
        detail = f": {last_error}" if last_error else ""
        raise MarketplaceError(f"no agent listing found at {source}{detail}")

    async def _resolve_entry(self, http: httpx.AsyncClient, entry: _Entry) -> dict[str, Any]:
        card = entry.card
        if card is None:
            validate_remote_url(entry.card_url, allow_local=self.allow_local)
            card = await self._get_json(http, entry.card_url, MAX_CARD_BYTES)
            if card is None:
                raise MarketplaceError(f"Agent Card not found at {entry.card_url}")
        return validate_agent_card(
            card, allowed_hosts=self.allowed_hosts, allow_local=self.allow_local
        )

    async def _load(self) -> list[MarketplaceAgent]:
        agents: list[MarketplaceAgent] = []
        problems: list[str] = []
        taken: set[str] = set()
        semaphore = asyncio.Semaphore(CARD_FETCH_CONCURRENCY)
        async with self._http_client() as http:
            for source in self.sources:
                try:
                    listing_url, raw = await self._read_listing(http, source, problems)
                except MarketplaceError as exc:
                    problems.append(str(exc))
                    continue
                entries: list[_Entry] = []
                for item in raw[: self.max_agents]:
                    try:
                        entries.append(parse_entry(item, listing_url))
                    except MarketplaceError as exc:
                        problems.append(f"{source}: {exc}")
                if len(raw) > self.max_agents:
                    problems.append(f"{source}: listing truncated to {self.max_agents} agents")

                async def resolve(entry: _Entry) -> dict[str, Any]:
                    async with semaphore:
                        return await self._resolve_entry(http, entry)

                cards = await asyncio.gather(
                    *(resolve(entry) for entry in entries), return_exceptions=True
                )
                for entry, card in zip(entries, cards, strict=True):
                    if isinstance(card, BaseException):
                        if not isinstance(card, Exception):
                            raise card
                        problems.append(f"{entry.card_url}: {type(card).__name__}: {card}")
                        continue
                    name = _clean(card["name"], 80) or "agent"
                    base_id = _slug(entry.hint_id or name)
                    agent_id, n = base_id, 2
                    while agent_id in taken:
                        agent_id, n = f"{base_id}-{n}", n + 1
                    taken.add(agent_id)
                    agents.append(
                        MarketplaceAgent(
                            id=agent_id,
                            name=name,
                            description=_clean(card.get("description"), 300),
                            card_url=entry.card_url,
                            source=source,
                            card=card,
                        )
                    )
                    if len(agents) >= self.max_agents:
                        break
        for problem in problems:
            LOG.warning("marketplace: %s", problem, extra={"event": "marketplace.problem"})
        self.problems = problems
        if not agents and problems:
            raise MarketplaceError("; ".join(problems[:5]))
        return agents

    async def agents(self, *, refresh: bool = False) -> list[MarketplaceAgent]:
        """Every listed agent with a valid card (cached; a failed refresh keeps the old list)."""
        async with self._lock:
            stale = time.monotonic() - self._loaded_at > self.refresh_seconds
            if self._agents is None or refresh or stale:
                try:
                    self._agents = await self._load()
                    self._loaded_at = time.monotonic()
                except MarketplaceError:
                    if self._agents is None:
                        raise
                    LOG.warning(
                        "marketplace refresh failed; keeping %d cached agents",
                        len(self._agents),
                        extra={"event": "marketplace.stale"},
                    )
                    self._loaded_at = time.monotonic()
            return list(self._agents)

    async def get(self, name: str) -> MarketplaceAgent:
        """An agent by its id or (case-insensitive) name."""
        agents = await self.agents()
        wanted = name.strip().lower()
        for agent in agents:
            if agent.id == wanted:
                return agent
        matches = [agent for agent in agents if agent.name.lower() == wanted]
        if len(matches) == 1:
            return matches[0]
        known = ", ".join(agent.id for agent in agents[:20]) or "none"
        raise LookupError(f"no marketplace agent named {name!r}; available: {known}")

    async def resolve(self, alias: str) -> AgentLocation:
        return (await self.get(alias)).location()

    # -- talking to agents ----------------------------------------------------------------

    def client(self, agent: MarketplaceAgent, **kwargs: Any):
        """An :class:`~lemonade_a2a.client.A2AClient` for ``agent`` (use with ``async with``)."""
        from lemonade_a2a.client import A2AClient

        kwargs.setdefault("timeout", self.timeout)
        return A2AClient(
            agent.endpoint_url,
            card=agent.card,
            allow_local=self.allow_local,
            allowed_hosts=self.allowed_hosts,
            **kwargs,
        )

    async def ask(
        self,
        agent: str | MarketplaceAgent,
        text: str,
        *,
        wait_seconds: float = 120.0,
        bearer_token: str | None = None,
        context_id: str | None = None,
        task_id: str | None = None,
        parts: Sequence[Any] = (),
    ):
        """Send ``text`` (and file/data ``parts``) to a listed agent; return its reply.

        Pass the ``context_id`` of an earlier reply to continue that conversation, and its
        ``task_id`` as well to answer a task that is waiting for input.
        """
        from lemonade_a2a.client.sdk import translate_error

        target = agent if isinstance(agent, MarketplaceAgent) else await self.get(agent)
        try:
            # A non-streaming send blocks until the remote task ends, so the HTTP timeout must
            # cover the whole wait; the listing timeout (self.timeout) would cut it short.
            http_timeout = max(self.timeout, wait_seconds)
            async with self.client(
                target, bearer_token=bearer_token, timeout=http_timeout
            ) as remote:
                return await remote.ask(
                    text,
                    wait_seconds=wait_seconds,
                    context_id=context_id,
                    task_id=task_id,
                    parts=parts,
                )
        except OutboundA2AError:
            raise
        except Exception as exc:
            raise translate_error(exc) from exc
