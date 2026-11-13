"""Stable prep identity shared by fresh preparation and resume selection."""

from hashlib import sha256


def stage_prep_id(
    *,
    run_id: str,
    stage: str,
    level: str,
    attempt: int,
    raw_hash: str,
    task_hash: str,
    approved_stage_plan_hash: str = "",
) -> str:
    identity = [
        run_id,
        stage,
        level,
        str(attempt),
        raw_hash,
        task_hash,
    ]
    if approved_stage_plan_hash:
        identity.append(approved_stage_plan_hash)
    digest = sha256("|".join(identity).encode("utf-8")).hexdigest()[:20]
    return f"stage-prep-{stage}-a{attempt}-{digest}"
