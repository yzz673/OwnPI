"""Local live-history compaction, following Codex's local branch."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from concurrent.futures import CancelledError
from dataclasses import replace
import logging
from typing import TYPE_CHECKING

from ownpi.controller.runtime_control_signal import RuntimeControlRequested
from ownpi.llm.errors import ProviderContextOverflowError
from ownpi.llm.provider_retry import RetryCancelled
from ownpi.runtime.limits.tool_output import TruncationPolicy, approx_token_count, truncate_text
from ownpi.runtime.native_tools.history import NativeHistory
from ownpi.runtime.native_tools.history_context import COMPACT_HOOK_EVENTS
from ownpi.runtime.native_tools.history_items import HistoryEntry, HistoryMessage
from ownpi.runtime.native_tools.history_prompt import call_key, output_key
from ownpi.runtime.native_tools.provider import ProviderOutputEnvelope, ProviderOutputItem, ProviderOutputItemKind
from ownpi.runtime.native_tools.provider_json import FrozenProviderJson
from ownpi.runtime.native_tools.provider_output_failure import ProviderOutputFailureFacts
from ownpi.runtime.native_tools.provider_output_attempt import iter_history_batches
from ownpi.runtime.native_tools.provider_input import NativeHistoryProviderInput

if TYPE_CHECKING:
    from ownpi.runtime.rollout.session import RolloutHistory

NativeCompactionInvoke = Callable[[NativeHistoryProviderInput], ProviderOutputEnvelope]
SUMMARY_PREFIX = ('Another language model started to solve this problem and produced a summary of its thinking process. '
                  'You also have access to the state of the tools that were used by that language model. '
                  'Use this to build on the work that has already been done and avoid duplicating work. '
                  'Here is the summary produced by the other language model, use the information in this summary to assist with your own analysis:')
COMPACT_PROMPT = '''You are performing a CONTEXT CHECKPOINT COMPACTION. Create a handoff summary for another LLM that will resume the task.

Include:
- Current progress and key decisions made
- Important context, constraints, or user preferences
- What remains to be done (clear next steps)
- Any critical data, examples, or references needed to continue

Be concise, structured, and focused on helping the next LLM seamlessly continue the work.'''


class HistoryCompactionError(RuntimeError):
    """Compaction failed at the current native loop boundary."""


class HistoryCompactionStopped(RetryCancelled):
    """A compaction hook stopped this turn."""


def compact_live_history(
    history: NativeHistory | RolloutHistory, request: NativeHistoryProviderInput, invoke: NativeCompactionInvoke,
    *, before: Callable[[], None] | None = None, after: Callable[[], None] | None = None,
) -> None:
    if before is not None:
        before()
    original = history.snapshot
    suffix = history.protected_suffix()
    temporary = (*(entry for entry in original if not (
        isinstance(entry.item, HistoryMessage)
        and entry.item.metadata.to_object().get('event_name') in COMPACT_HOOK_EVENTS
        and entry.item.metadata.to_object().get('delivery_id')
    )), HistoryEntry(HistoryMessage('user', COMPACT_PROMPT)))
    while True:
        prompt_history = NativeHistory(history.policy)
        prompt_history.replace(temporary)
        try:
            response = invoke(replace(request, items=prompt_history.for_prompt()))
        except (RetryCancelled, RuntimeControlRequested, CancelledError, asyncio.CancelledError) as error:
            _record_partial(history, error)
            raise
        except ProviderContextOverflowError as error:
            _record_partial(history, error)
            if len(temporary) <= 1:
                raise HistoryCompactionError(str(error)) from error
            temporary = _remove_first_pair(temporary)
            continue
        except Exception as error:
            _record_partial(history, error)
            if isinstance(error, (TypeError, AssertionError, AttributeError, KeyError)):
                raise
            # Transport owns its existing bounded transient retries.
            raise HistoryCompactionError(str(error)) from error
        history.record_provider(response)
        # Returning an envelope is the fact layer's completion boundary. Raw
        # status metadata is not a second lifecycle decision for this consumer.
        summary = next((_message_text(item) for item in reversed(response.completed_items)
                        if item.kind == ProviderOutputItemKind.MESSAGE
                        and item.provider_payload.to_object().get('role', 'assistant') == 'assistant'), '')
        break
    source = original[:len(original) - len(suffix)] if suffix else original
    selected = _recent_users(source)
    selected.append(HistoryEntry(HistoryMessage('user', SUMMARY_PREFIX + '\n' + summary)))
    history.replace((*selected, *suffix))
    if after is not None:
        after()


def _recent_users(source: tuple[HistoryEntry, ...]) -> list[HistoryEntry]:
    selected: list[HistoryEntry] = []
    remaining = 20_000
    for entry in reversed(source):
        if remaining == 0:
            break
        item = entry.item
        role: str | None
        if isinstance(item, HistoryMessage):
            role, text, metadata = item.role, item.text, item.metadata
        elif isinstance(item, ProviderOutputItem) and item.kind == ProviderOutputItemKind.MESSAGE:
            payload = item.provider_payload.to_object()
            role, text = payload.get('role'), _message_text(item)
            metadata = FrozenProviderJson.from_value({
                **item.provider_metadata.to_object(),
                **{key: payload[key] for key in ('internal_chat_message_metadata_passthrough',) if key in payload},
            })
        else:
            continue
        if role != 'user' or not text or text.startswith(SUMMARY_PREFIX + '\n'):
            continue
        tokens = approx_token_count(text)
        selected.append(replace(entry, item=HistoryMessage(
            'user', truncate_text(text, TruncationPolicy.tokens(remaining)), metadata,
        )))
        if tokens > remaining:
            break
        remaining -= tokens
    selected.reverse()
    return selected


def _message_text(item: ProviderOutputItem) -> str:
    content = item.provider_payload.to_object().get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(block['text'] for block in content if isinstance(block, dict)
                         and block.get('type') in {'input_text', 'output_text', 'text'}
                         and isinstance(block.get('text'), str))
    return item.text


def _record_partial(history: NativeHistory | RolloutHistory, error: BaseException) -> None:
    facts = getattr(error, 'provider_output_failure', None)
    if facts is None:
        return
    try:
        if not isinstance(facts, ProviderOutputFailureFacts):
            raise TypeError('compaction failure facts must be ProviderOutputFailureFacts')
        history.record_items(HistoryEntry(item, batch.exchange_id, item.sequence_index)
                             for batch in iter_history_batches(facts) for item in batch.completed_items)
    except Exception as recording_error:
        error.add_note(f'Failure facts could not be recorded: {recording_error!r}')
        logging.getLogger(__name__).exception('Compaction failure fact recording failed')


def _remove_first_pair(entries: tuple[HistoryEntry, ...]) -> tuple[HistoryEntry, ...]:
    first = entries[0]
    key = call_key(first)
    counterpart = output_key
    if key is None:
        key = output_key(first)
        counterpart = call_key
    if key is None:
        return entries[1:]
    # Codex removes one matching counterpart, not every occurrence of an ID.
    match = next((index for index, entry in enumerate(entries[1:], 1) if counterpart(entry) == key), None)
    return tuple(entry for index, entry in enumerate(entries[1:], 1) if index != match)
