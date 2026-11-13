from __future__ import annotations

from dataclasses import dataclass


CRITIC_FEEDBACK_MISSING_NUDGE = "Complete your feedback and call submit_feedback()."
CORE_FEEDBACK_INTENT_MISSING_NUDGE = (
    "Check every current-stage task requirement or actionable Reviewer finding individually "
    "against actual artifacts and evidence. Continue any unfinished work. Only after verifying "
    "that all required work is complete, call request_review(). Call complete_feedback() only "
    "after the current Reviewer feedback is No findings. Do not request review of partial work."
)
ANALYSIS_FEEDBACK_AND_ROUTE_MISSING_NUDGE = (
    "Complete the Analysis Memo, submit feedback with submit_feedback(), then call "
    "next_stage(target_stage) with one stage exposed by the tool for the active workflow plan."
)
ANALYSIS_FEEDBACK_MISSING_NUDGE = (
    "Complete the Analysis Memo and submit feedback with submit_feedback() before "
    "calling next_stage(target_stage)."
)
ANALYSIS_ROUTE_MISSING_NUDGE = (
    "Call next_stage(target_stage) with one stage exposed by the tool for the active "
    "workflow plan."
)


@dataclass(frozen=True)
class RoleLoopReceiptSnapshot:
    feedback_submitted: bool = False
    request_review: bool = False
    complete_feedback: bool = False
    next_stage: bool = False


@dataclass(frozen=True)
class RoleLoopContinuationPolicy:
    name: str
    required_all: tuple[str, ...] = ()
    required_exactly_one: tuple[str, ...] = ()
    missing_all_nudge: str = ""
    missing_by_receipt: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        declared = (*self.required_all, *self.required_exactly_one)
        if not self.name.strip() or not declared:
            raise ValueError("role continuation policy requires a name and receipts")
        if len(set(declared)) != len(declared):
            raise ValueError("role continuation receipt names must be unique")
        if self.required_all and self.required_exactly_one:
            raise ValueError(
                "one policy cannot mix all-of and exactly-one requirements"
            )


@dataclass(frozen=True)
class RoleLoopContinuationDecision:
    complete: bool
    reason: str
    nudge: str = ""


def evaluate_role_loop_continuation(
    *,
    policy: RoleLoopContinuationPolicy,
    receipts: RoleLoopReceiptSnapshot,
) -> RoleLoopContinuationDecision:
    values = {
        "submit_feedback": receipts.feedback_submitted,
        "request_review": receipts.request_review,
        "complete_feedback": receipts.complete_feedback,
        "next_stage": receipts.next_stage,
    }
    if policy.required_exactly_one:
        present = sum(
            bool(values.get(name, False)) for name in policy.required_exactly_one
        )
        if present == 1:
            return RoleLoopContinuationDecision(
                True, "required_terminal_receipt_present"
            )
        if present > 1:
            raise ValueError("mutually exclusive terminal receipts are both present")
        return RoleLoopContinuationDecision(
            False,
            "required_terminal_receipt_missing",
            policy.missing_all_nudge,
        )
    missing = tuple(name for name in policy.required_all if not values.get(name, False))
    if not missing:
        return RoleLoopContinuationDecision(True, "required_receipts_present")
    nudges = dict(policy.missing_by_receipt)
    if len(missing) == len(policy.required_all):
        nudge = policy.missing_all_nudge
    elif len(missing) == 1:
        nudge = nudges.get(missing[0], policy.missing_all_nudge)
    else:
        nudge = policy.missing_all_nudge
    return RoleLoopContinuationDecision(False, "required_receipt_missing", nudge)


def critic_feedback_continuation_policy() -> RoleLoopContinuationPolicy:
    return RoleLoopContinuationPolicy(
        name="critic_feedback_submission",
        required_all=("submit_feedback",),
        missing_all_nudge=CRITIC_FEEDBACK_MISSING_NUDGE,
    )


def core_feedback_continuation_policy(*, direct: bool = False) -> RoleLoopContinuationPolicy:
    return RoleLoopContinuationPolicy(
        name="core_feedback_terminal_intent",
        required_exactly_one=("request_review", "complete_feedback"),
        missing_all_nudge=(
            'Finish the current user request and call request_review(). After Reviewer feedback, '
            'address findings and request review again, or call complete_feedback() when no issue remains.'
        ) if direct else CORE_FEEDBACK_INTENT_MISSING_NUDGE,
    )


def analysis_continuation_policy() -> RoleLoopContinuationPolicy:
    return RoleLoopContinuationPolicy(
        name="analysis_feedback_and_route",
        required_all=("submit_feedback", "next_stage"),
        missing_all_nudge=ANALYSIS_FEEDBACK_AND_ROUTE_MISSING_NUDGE,
        missing_by_receipt=(
            (
                "submit_feedback",
                ANALYSIS_FEEDBACK_MISSING_NUDGE,
            ),
            (
                "next_stage",
                ANALYSIS_ROUTE_MISSING_NUDGE,
            ),
        ),
    )


__all__ = [
    "RoleLoopContinuationDecision",
    "RoleLoopContinuationPolicy",
    "RoleLoopReceiptSnapshot",
    "analysis_continuation_policy",
    "core_feedback_continuation_policy",
    "critic_feedback_continuation_policy",
    "evaluate_role_loop_continuation",
]
