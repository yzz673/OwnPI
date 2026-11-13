CAVEATED_SUCCESS_RUN_STATUSES = frozenset(
    {'completed_with_caveats', 'success_with_caveats', 'passed_with_caveats'}
)

PARTIAL_DELIVERABLE_RUN_STATUSES = frozenset({'partial_deliverable', 'partial_with_deliverable'})

def classify_non_success_run_status(run_status: str) -> tuple[str, str]:
    normalized = str(run_status or '').strip()
    lowered = normalized.lower()
    if lowered in CAVEATED_SUCCESS_RUN_STATUSES:
        return 'success_with_caveats', 'successful_completion_with_caveats'
    if lowered in PARTIAL_DELIVERABLE_RUN_STATUSES:
        return 'partial', 'partial_deliverable'
    if lowered == 'partial':
        return 'partial', 'partial_deliverable'
    if 'dataset' in lowered and 'missing' in lowered:
        return 'blocked_missing_required_dataset', 'external_prerequisite_missing'
    if lowered == 'blocked':
        return 'blocked_execution_not_ready', 'execution_not_ready'
    if lowered.startswith('failed_'):
        return normalized, 'execution_not_ready'
    if lowered in {'fail', 'failed'}:
        return 'failed', 'execution_failed'
    if not normalized:
        return 'unknown_failure', 'execution_not_ready'
    return normalized, 'execution_not_ready'
