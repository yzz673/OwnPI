"""Display provider-supplied message sources without interpreting text offsets."""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from urllib.parse import urlsplit

from ownpi.runtime.native_tools.provider import ProviderOutputItem


def _http_url(value: object) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    # Delimiters and controls must not escape a Markdown autolink or reach OSC 8.
    if any(ch.isspace() or unicodedata.category(ch).startswith("C") or ch in '<>"`\\'
           for ch in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            return None
        if parsed.username is not None or parsed.password is not None:
            return None
        host = parsed.hostname
        parsed.port  # Validate an explicitly supplied port.
        if not host or "%" in host:
            return None
        try:
            ipaddress.ip_address(host)
        except ValueError:
            domain = host.rstrip(".").encode("idna").decode("ascii")
            labels = domain.split(".")
            if len(domain) > 253 or any(
                not re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in labels
            ):
                return None
    except (ValueError, UnicodeError):
        return None
    return value


def message_source_text(item: ProviderOutputItem) -> str:
    """List only URLs explicitly attached to this message, not claim mappings.

    Offset units vary across providers; the raw answer and annotations are left
    untouched, and no position or claim association is inferred from them.
    """
    urls: list[str] = []
    seen: set[str] = set()
    for frozen in item.content_blocks:
        block = frozen.to_value()
        if not isinstance(block, dict):
            continue
        annotations = block.get("annotations")
        if not isinstance(annotations, list):
            continue
        for annotation in annotations:
            if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                continue
            url = _http_url(annotation.get("url"))
            if url is not None and url not in seen:
                seen.add(url)
                urls.append(url)
    if not urls:
        return ""
    # Explicit Markdown links decode entities in both label and destination;
    # autolinks do not, and can therefore silently change query strings.
    def link(url: str) -> str:
        escaped = url.replace("&", "&amp;")
        label = re.sub(r"([\[\]*_])", r"\\\1", escaped)
        return f"- [{label}](<{escaped}>)"

    links = "\n".join(link(url) for url in urls)
    return f"提供方附随此消息的链接（未逐条对应正文）：\n\n{links}"
