from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, quote_plus, urlparse, urljoin
from xml.etree import ElementTree

from bs4 import BeautifulSoup

from ownpi.runtime.limits.resources import MAX_PAPER_FETCH_INPUT_CHARS
from ownpi.tools.web_tools import _validate_public_http_url


_DOI_PATTERN = re.compile(r"^10\.\d{4,9}/\S+$", re.IGNORECASE)
_ARXIV_PATTERN = re.compile(
    r"^(?:arxiv:)?(?P<id>\d{4}\.\d{4,5}(?:v\d+)?)$", re.IGNORECASE
)


@dataclass(frozen=True)
class PaperIdentity:
    input_value: str
    kind: str
    doi: str = ""
    arxiv_id: str = ""
    canonical_url: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "input": self.input_value,
            "kind": self.kind,
            "doi": self.doi,
            "arxiv_id": self.arxiv_id,
            "canonical_url": self.canonical_url,
        }


@dataclass
class PaperCandidate:
    source: str
    title: str = ""
    authors: list[str] = field(default_factory=list)
    publication_date: str = ""
    abstract: str = ""
    doi: str = ""
    landing_url: str = ""
    pdf_url: str = ""
    content_urls: list[str] = field(default_factory=list)
    arxiv_id: str = ""
    body: str = ""
    publication_type: str = ""
    publication_year: str = ""
    publication_fields: dict[str, str] = field(default_factory=dict)

    def has_metadata(self) -> bool:
        return any((self.title, self.authors, self.publication_date, self.doi))


def _normalize_identity(raw: str) -> tuple[PaperIdentity | None, str]:
    if not raw:
        return None, "identifier or url is required"
    if len(raw) > MAX_PAPER_FETCH_INPUT_CHARS:
        return None, f"identifier exceeds {MAX_PAPER_FETCH_INPUT_CHARS} character limit"
    candidate = raw.strip()
    if candidate.lower().startswith("doi:"):
        candidate = candidate[4:].strip()
    arxiv_match = _ARXIV_PATTERN.fullmatch(candidate)
    if arxiv_match:
        arxiv_id = arxiv_match.group("id")
        return PaperIdentity(
            raw,
            "arxiv",
            arxiv_id=arxiv_id,
            canonical_url=f"https://arxiv.org/abs/{arxiv_id}",
        ), ""
    if _DOI_PATTERN.fullmatch(candidate):
        doi = candidate.rstrip(".,;)").lower()
        return PaperIdentity(
            raw, "doi", doi=doi, canonical_url=f"https://doi.org/{doi}"
        ), ""
    parsed = urlparse(candidate)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return None, "identifier must be a DOI, arXiv identifier, or HTTP(S) URL"
    try:
        _validate_public_http_url(candidate)
    except ValueError as exc:
        return None, str(exc)
    host = parsed.netloc.casefold()
    path = parsed.path.strip("/")
    if host in {"doi.org", "dx.doi.org"} and _DOI_PATTERN.fullmatch(path):
        doi = path.rstrip(".,;)").lower()
        return PaperIdentity(
            raw, "doi", doi=doi, canonical_url=f"https://doi.org/{doi}"
        ), ""
    if host == "arxiv.org" or host.endswith(".arxiv.org"):
        arxiv_id = re.sub(r"^(?:abs|pdf)/", "", path, flags=re.IGNORECASE).removesuffix(
            ".pdf"
        )
        match = _ARXIV_PATTERN.fullmatch(arxiv_id)
        if match:
            normalized = match.group("id")
            return PaperIdentity(
                raw,
                "arxiv",
                arxiv_id=normalized,
                canonical_url=f"https://arxiv.org/abs/{normalized}",
            ), ""
    return PaperIdentity(raw, "url", canonical_url=candidate), ""


def _metadata_routes(identity: PaperIdentity) -> list[tuple[str, str, Any]]:
    if identity.kind == "arxiv":
        url = f"https://export.arxiv.org/api/query?id_list={quote_plus(identity.arxiv_id)}"
        return [("arxiv", url, _parse_arxiv)]
    if identity.kind == "doi":
        encoded_doi = quote(identity.doi, safe="")
        openalex_id = quote(f"https://doi.org/{identity.doi}", safe=":/")
        return [
            (
                "openalex",
                f"https://api.openalex.org/works/{openalex_id}",
                _parse_openalex,
            ),
            (
                "crossref",
                f"https://api.crossref.org/works/{encoded_doi}",
                _parse_crossref,
            ),
            (
                "europe_pmc",
                "https://www.ebi.ac.uk/europepmc/webservices/rest/search?"
                f"query={quote_plus(f'DOI:{identity.doi}')}&format=json&resultType=core&pageSize=1",
                _parse_europe_pmc,
            ),
            ("publisher", identity.canonical_url, _parse_publisher_html),
        ]
    if _is_pdf_url(identity.canonical_url):
        return []
    return [("publisher", identity.canonical_url, _parse_publisher_html)]


def _parse_openalex(payload: str, identity: PaperIdentity) -> PaperCandidate:
    data = json.loads(payload)
    abstract_index = data.get("abstract_inverted_index") or {}
    abstract = _abstract_from_inverted_index(abstract_index)
    primary = data.get("primary_location") or {}
    best_oa = data.get("best_oa_location") or {}
    biblio = data.get('biblio') or {}
    pages = list(dict.fromkeys(str(biblio[k]) for k in ('first_page', 'last_page') if biblio.get(k)))
    return PaperCandidate(
        source="openalex",
        title=str(data.get("title") or data.get("display_name") or ""),
        authors=[
            str((item.get("author") or {}).get("display_name") or "")
            for item in data.get("authorships") or []
            if isinstance(item, dict)
        ],
        publication_date=str(data.get("publication_date") or ""),
        publication_type=str(data.get('type_crossref') or data.get('type') or ''),
        publication_year=str(data.get('publication_year') or data.get('publication_date') or ''),
        publication_fields=_publication_fields({
            'journal': (primary.get('source') or {}).get('display_name'),
            'volume': biblio.get('volume'), 'number': biblio.get('issue'), 'pages': '--'.join(pages),
        }),
        abstract=abstract,
        doi=_doi_from_value(str(data.get("doi") or "")),
        landing_url=str(
            primary.get("landing_page_url") or best_oa.get("landing_page_url") or ""
        ),
        pdf_url=str(primary.get("pdf_url") or best_oa.get("pdf_url") or ""),
        content_urls=[
            str(item.get("pdf_url"))
            for item in [primary, best_oa, *(data.get("locations") or [])]
            if isinstance(item, dict) and item.get("pdf_url")
        ],
    )


def _parse_crossref(payload: str, identity: PaperIdentity) -> PaperCandidate:
    message = json.loads(payload).get("message") or {}
    return _crossref_candidate(message, identity)


def _crossref_candidate(message: dict[str, Any], identity: PaperIdentity) -> PaperCandidate:
    links = message.get("link") or []
    pdf_url = next(
        (
            str(item.get("URL") or "")
            for item in links
            if isinstance(item, dict)
            and "pdf" in str(item.get("content-type") or "").casefold()
        ),
        "",
    )
    return PaperCandidate(
        source="crossref",
        title=_first_text(message.get("title")),
        authors=[
            " ".join(
                part
                for part in (
                    str(item.get("given") or ""),
                    str(item.get("family") or ""),
                )
                if part
            ).strip()
            for item in message.get("author") or []
            if isinstance(item, dict)
        ],
        publication_date=_crossref_date(message),
        publication_type=str(message.get('type') or ''),
        publication_year=_crossref_publication_year(message),
        publication_fields=_publication_fields({
            'journal': _first_text(message.get('container-title')),
            'volume': message.get('volume'), 'number': message.get('issue'),
            'pages': message.get('page') or message.get('article-number'),
            'publisher': message.get('publisher'), 'edition': message.get('edition-number'),
        }),
        abstract=_clean_markup(str(message.get("abstract") or "")),
        doi=str(message.get("DOI") or ""),
        landing_url=str(message.get("URL") or identity.canonical_url),
        pdf_url=pdf_url,
        content_urls=[
            str(item["URL"])
            for item in links
            if isinstance(item, dict)
            and item.get("URL")
            and "pdf" in str(item.get("content-type", "")).lower()
        ],
    )


def _parse_europe_pmc(payload: str, identity: PaperIdentity) -> PaperCandidate:
    results = (json.loads(payload).get("resultList") or {}).get("result") or []
    result = results[0] if results and isinstance(results[0], dict) else {}
    return PaperCandidate(
        source="europe_pmc",
        title=str(result.get("title") or ""),
        authors=[
            item.strip()
            for item in str(result.get("authorString") or "").split(",")
            if item.strip()
        ],
        publication_date=str(
            result.get("firstPublicationDate")
            or result.get("journalInfo", {}).get("printPublicationDate")
            or ""
        ),
        publication_type='journal-article' if (result.get('journalInfo') or {}).get('journal') else '',
        publication_year=str(result.get('pubYear') or result.get('firstPublicationDate') or ''),
        publication_fields=_publication_fields({
            'journal': ((result.get('journalInfo') or {}).get('journal') or {}).get('title'),
            'volume': (result.get('journalInfo') or {}).get('volume'),
            'number': (result.get('journalInfo') or {}).get('issue'),
            'pages': result.get('pageInfo'),
        }),
        abstract=str(result.get("abstractText") or ""),
        doi=str(result.get("doi") or ""),
        landing_url=(
            f"https://europepmc.org/article/{result.get('source')}/{result.get('id')}"
            if result.get("id")
            else ""
        ),
        pdf_url=str(result.get("pdfUrl") or ""),
        content_urls=[
            str(item["url"])
            for item in (result.get("fullTextUrlList") or {}).get("fullTextUrl", [])
            if isinstance(item, dict)
            and item.get("url")
            and item.get("availability") == "Open access"
        ],
    )


def _parse_arxiv(payload: str, identity: PaperIdentity) -> PaperCandidate:
    root = ElementTree.fromstring(payload)
    entry = next(
        (element for element in root.iter() if _local_name(element.tag) == "entry"),
        None,
    )
    if entry is None:
        return PaperCandidate(source="arxiv")
    links = [element.attrib for element in entry if _local_name(element.tag) == "link"]
    returned, error = _normalize_identity(_xml_child_text(entry, "id"))
    if error or returned is None or returned.kind != "arxiv":
        raise ValueError("arxiv response is not a paper entry")
    if re.sub(r"v\d+$", "", returned.arxiv_id) != re.sub(
        r"v\d+$", "", identity.arxiv_id
    ):
        raise ValueError("arxiv identity conflict")
    if (
        re.search(r"v\d+$", identity.arxiv_id)
        and returned.arxiv_id != identity.arxiv_id
    ):
        raise ValueError("arxiv version conflict")
    return PaperCandidate(
        source="arxiv",
        arxiv_id=returned.arxiv_id,
        title=_xml_child_text(entry, "title"),
        authors=[
            _xml_child_text(author, "name")
            for author in entry
            if _local_name(author.tag) == "author"
        ],
        publication_date=_xml_child_text(entry, "published"),
        publication_type='posted-content',
        publication_year=_xml_child_text(entry, 'published'),
        publication_fields=_publication_fields({'howpublished': 'arXiv', 'note': _xml_child_text(entry, 'journal_ref')}),
        abstract=_xml_child_text(entry, "summary"),
        doi=_xml_child_text(entry, "doi"),
        landing_url=_xml_child_text(entry, "id") or identity.canonical_url,
        pdf_url=next(
            (
                str(link.get("href") or "")
                for link in links
                if link.get("title") == "pdf" or link.get("type") == "application/pdf"
            ),
            "",
        ),
    )


def _parse_publisher_html(payload: str, identity: PaperIdentity) -> PaperCandidate:
    soup = BeautifulSoup(payload, "html.parser")
    title = _meta_value(soup, "citation_title", "dc.title")
    doi = _doi_from_value(_meta_value(soup, "citation_doi", "dc.identifier"))
    if not title or not (
        _DOI_PATTERN.fullmatch(doi) or _meta_values(soup, "citation_author")
    ):
        return PaperCandidate(source="publisher")
    urls = [_meta_value(soup, "citation_pdf_url")]
    urls.extend(
        str(a.get("href"))
        for a in soup.find_all("a", href=True)
        if _is_pdf_url(str(a.get("href"))) or a.get("type") == "application/pdf"
    )
    urls = [urljoin(identity.canonical_url, url) for url in urls if url]
    body = soup.select_one('[itemprop="articleBody"]')
    return PaperCandidate(
        source="publisher",
        title=title,
        authors=_meta_values(soup, "citation_author"),
        publication_date=_meta_value(
            soup, "citation_publication_date", "dc.date", "article:published_time"
        ),
        publication_type='journal-article' if _meta_value(soup, 'citation_journal_title') else '',
        publication_year=_meta_value(soup, 'citation_publication_date', 'dc.date', 'article:published_time'),
        publication_fields=_publication_fields({
            'journal': _meta_value(soup, 'citation_journal_title'),
            'booktitle': _meta_value(soup, 'citation_conference_title'),
            'volume': _meta_value(soup, 'citation_volume'), 'number': _meta_value(soup, 'citation_issue'),
            'pages': '--'.join(dict.fromkeys(filter(None, (
                _meta_value(soup, 'citation_firstpage'), _meta_value(soup, 'citation_lastpage'),
            )))),
            'publisher': _meta_value(soup, 'citation_publisher'),
        }),
        abstract=_meta_value(soup, "citation_abstract", "dc.description"),
        doi=doi if _DOI_PATTERN.fullmatch(doi) else "",
        landing_url=identity.canonical_url,
        pdf_url=urls[0] if urls else "",
        content_urls=urls,
        body=body.get_text(" ", strip=True) if body else "",
    )


def _publication_fields(values: dict[str, Any]) -> dict[str, str]:
    return {key: str(value) for key, value in values.items() if value}


def _crossref_publication_year(message: dict[str, Any]) -> str:
    for key in ('published', 'published-print', 'published-online', 'issued'):
        parts = (message.get(key) or {}).get('date-parts') or []
        if parts and isinstance(parts[0], list) and parts[0]:
            return str(parts[0][0])
    return ''


def _abstract_from_inverted_index(index: Any) -> str:
    if not isinstance(index, dict):
        return ""
    positions: list[tuple[int, str]] = []
    for word, offsets in index.items():
        if not isinstance(offsets, list):
            continue
        positions.extend(
            (int(offset), str(word)) for offset in offsets if isinstance(offset, int)
        )
    return " ".join(word for _, word in sorted(positions))


def _crossref_date(message: dict[str, Any]) -> str:
    for key in ("published-print", "published-online", "issued", "created"):
        parts = (message.get(key) or {}).get("date-parts") or []
        if parts and isinstance(parts[0], list):
            return "-".join(str(value) for value in parts[0][:3])
    return ""


def _xml_child_text(element: ElementTree.Element, local_name: str) -> str:
    child = next(
        (item for item in element.iter() if _local_name(item.tag) == local_name), None
    )
    return _clean_text("".join(child.itertext())) if child is not None else ""


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _meta_values(soup: BeautifulSoup, key: str) -> list[str]:
    values: list[str] = []
    for meta in soup.find_all("meta"):
        name = str(meta.get("name") or meta.get("property") or "").casefold()
        if name == key.casefold():
            value = _clean_text(str(meta.get("content") or ""))
            if value:
                values.append(value)
    return values


def _meta_value(soup: BeautifulSoup, *keys: str) -> str:
    for key in keys:
        values = _meta_values(soup, key)
        if values:
            return values[0]
    return ""


def _first_text(value: Any) -> str:
    if isinstance(value, list):
        return str(value[0] or "") if value else ""
    return str(value or "")


def _doi_from_value(value: str) -> str:
    normalized = value.strip()
    for prefix in ("https://doi.org/", "http://doi.org/", "doi:"):
        if normalized.casefold().startswith(prefix):
            return normalized[len(prefix) :]
    return normalized


def _clean_markup(value: str) -> str:
    return _clean_text(BeautifulSoup(value, "html.parser").get_text(" ", strip=True))


def _clean_text(value: str) -> str:
    return " ".join(str(value or "").split())


def _is_pdf_url(value: str) -> bool:
    return urlparse(value).path.casefold().endswith(".pdf")
