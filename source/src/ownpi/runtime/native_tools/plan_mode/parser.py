from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposedPlanParseResult:
    plan_text: str = ""
    visible_text: str = ""
    block_count: int = 0
    has_unclosed_block: bool = False
    has_invalid_nested_block: bool = False

    @property
    def found(self) -> bool:
        return self.block_count > 0


class ProposedPlanParser:
    """Provider-neutral parser for Codex-style proposed-plan blocks."""

    def __init__(
        self,
        *,
        start_tag: str = "<proposed_plan>",
        end_tag: str = "</proposed_plan>",
    ) -> None:
        if not start_tag or not end_tag or start_tag == end_tag:
            raise ValueError("proposed-plan tags must be distinct and non-empty")
        self._start_tag = start_tag
        self._end_tag = end_tag

    def parse(self, text: str) -> ProposedPlanParseResult:
        source = str(text or "")
        visible: list[str] = []
        plans: list[str] = []
        cursor = 0
        unclosed = False
        invalid_nested = False

        while cursor < len(source):
            start = source.find(self._start_tag, cursor)
            if start < 0:
                visible.append(source[cursor:])
                break
            visible.append(source[cursor:start])
            content_start = start + len(self._start_tag)
            end = source.find(self._end_tag, content_start)
            nested = source.find(self._start_tag, content_start, end if end >= 0 else len(source))
            if nested >= 0:
                invalid_nested = True
            if end < 0:
                plans.append(source[content_start:])
                unclosed = True
                cursor = len(source)
                break
            plans.append(source[content_start:end])
            cursor = end + len(self._end_tag)

        return ProposedPlanParseResult(
            plan_text="\n\n".join(item.strip() for item in plans if item.strip()),
            visible_text="".join(visible),
            block_count=len(plans),
            has_unclosed_block=unclosed,
            has_invalid_nested_block=invalid_nested,
        )


class ProposedPlanStreamParser:
    """Small incremental facade used when provider chunks are available."""

    def __init__(self, parser: ProposedPlanParser | None = None) -> None:
        self._parser = parser or ProposedPlanParser()
        self._chunks: list[str] = []

    def feed(self, chunk: str) -> ProposedPlanParseResult:
        self._chunks.append(str(chunk or ""))
        return self.current()

    def current(self) -> ProposedPlanParseResult:
        return self._parser.parse("".join(self._chunks))

    def finish(self) -> ProposedPlanParseResult:
        return self.current()


__all__ = [
    "ProposedPlanParseResult",
    "ProposedPlanParser",
    "ProposedPlanStreamParser",
]
