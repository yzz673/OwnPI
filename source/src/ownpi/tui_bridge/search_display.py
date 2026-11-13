"""Read-only explanations of supported arXiv field queries.

Grammar reference: https://info.arxiv.org/help/api/user-manual.html#51-details-of-query-construction
Unsupported syntax is retained verbatim rather than assigned an inferred meaning.
"""
from __future__ import annotations

import re

_FIELDS = {"ti": "Title", "au": "Author", "abs": "Abstract", "co": "Comment",
           "jr": "Journal reference", "cat": "Subject category", "rn": "Report number",
           "id": "Paper ID", "all": "Any field"}
_TOKEN = re.compile(r'(?P<field>ti|au|abs|co|jr|cat|rn|id|all):(?P<term>"[^"\\]*"|[^\s()"\\:\[\]]+)|(?P<op>ANDNOT|AND|OR)\b|(?P<paren>[()])')


def describe_search_query(query: str, backend: str) -> str:
    if backend.lower() != "arxiv":
        return query
    tokens: list[tuple[str, str]] = []
    offset, depth = 0, 0
    expects_term = True
    while offset < len(query):
        if query[offset].isspace():
            offset += 1
            continue
        match = _TOKEN.match(query, offset)
        if match is None:
            return query
        offset = match.end()
        if match.group("field"):
            if not expects_term:
                return query
            tokens.append((match.group("field"), match.group("term")))
            expects_term = False
        elif match.group("op"):
            if expects_term:
                return query
            tokens.append(("op", match.group("op")))
            expects_term = True
        elif match.group("paren") == "(":
            if not expects_term:
                return query
            depth += 1
            tokens.append(("paren", "("))
        else:
            if expects_term or depth == 0:
                return query
            depth -= 1
            tokens.append(("paren", ")"))
    if not tokens or expects_term or depth:
        return query
    terms = [(field, term) for field, term in tokens if field in _FIELDS]
    operators = [value for kind, value in tokens if kind == "op"]
    if (all(field == "ti" and term.startswith('"') for field, term in terms)
            and not any(kind == "paren" for kind, _ in tokens)
            and (not operators or set(operators) in ({"AND"}, {"OR"}))):
        prefix = "Title contains: " if not operators else (
            "Title contains both: " if len(terms) == 2 else "Title contains all of: "
        ) if operators[0] == "AND" else "Title contains any of: "
        return prefix + ", ".join(f'"{term[1:-1]}"' for _, term in terms)
    parts = []
    for kind, value in tokens:
        if kind in _FIELDS:
            term = value[1:-1] if value.startswith('"') else value
            parts.append(f'{_FIELDS[kind]} matches: "{term}"')
        elif kind == "op":
            parts.append({"AND": " and ", "OR": " or ", "ANDNOT": " excluding "}[value])
        else:
            parts.append("(" if value == "(" else ")")
    return "".join(parts)
