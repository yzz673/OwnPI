use serde_json::Value;

use crate::bottom_pane::PermissionFileChange;
use crate::file_change::{FileChange, FileChangeEntry, FileChanges};

pub(crate) const DISPLAY_ITEM_SCHEMA: &str = "ownpi.tui.item.v1";

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct AuditContext {
    pub(crate) audit_id: String,
    pub(crate) run_id: String,
    pub(crate) role: String,
    pub(crate) stage: String,
    pub(crate) attempt: u64,
    pub(crate) review_round: u64,
}

impl AuditContext {
    fn parse(value: &Value) -> Option<Self> {
        let context = Self {
            audit_id: value.get("audit_id")?.as_str()?.to_string(),
            run_id: value.get("run_id")?.as_str()?.to_string(),
            role: value.get("role")?.as_str()?.to_string(),
            stage: value.get("stage")?.as_str()?.to_string(),
            attempt: value.get("attempt")?.as_u64()?,
            review_round: value.get("review_round")?.as_u64()?,
        };
        (!context.audit_id.is_empty()
            && !context.run_id.is_empty()
            && !context.stage.is_empty()
            && context.attempt > 0
            && context.review_round > 0
            && matches!(context.role.as_str(), "readiness" | "reviewer"))
        .then_some(context)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct DisplayItemCommon {
    pub(crate) audit_context: Option<AuditContext>,
    pub(crate) actor_label: String,
    pub(crate) item_id: String,
    pub(crate) call_id: String,
    pub(crate) tool_name: String,
    pub(crate) status: String,
    pub(crate) action: String,
    pub(crate) raw_source: String,
    pub(crate) projection_phase: String,
    pub(crate) receipt_state: String,
    pub(crate) not_run_reason: String,
    pub(crate) evidence_revision: u64,
    pub(crate) retry_group_id: String,
    pub(crate) attempt_index: u64,
    pub(crate) correction_of: String,
    pub(crate) activity_group: ActivityGroupMeta,
}

/// Lifecycle metadata shared by every tool-shaped thread item.
///
/// The Bridge already emits these fields for commands, web searches, file
/// changes, MCP calls, images, and background terminals. Keeping them on the
/// common envelope prevents non-command terminal events from losing the
/// identity required to close their matching permission receipt.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ActivityGroupMeta {
    pub(crate) aggregation_batch: String,
    pub(crate) semantic_group_state: String,
    pub(crate) semantic_group_visible_total: u64,
    pub(crate) semantic_group_terminal_total: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct CommandExecutionPayload {
    pub(crate) command: String,
    pub(crate) requested_command: String,
    pub(crate) effective_command: String,
    pub(crate) cwd: String,
    pub(crate) target: String,
    pub(crate) display_target: String,
    pub(crate) result_state: String,
    pub(crate) citation_verification: CitationVerificationPayload,
    pub(crate) citation_candidates: Vec<CitationCandidatePayload>,
    pub(crate) citation_guidance: String,
    pub(crate) requested_identifier: String,
    pub(crate) resolved_identifier: String,
    pub(crate) requested_author: String,
    pub(crate) requested_title: String,
    pub(crate) requested_year: String,
    pub(crate) resolved_title: String,
    pub(crate) resolved_authors: Vec<String>,
    pub(crate) resolved_year: String,
    pub(crate) content_access_status: String,
    pub(crate) identity_conflicts: Vec<String>,
    pub(crate) diagnostics: Vec<String>,
    pub(crate) path_exists: Option<bool>,
    pub(crate) query: String,
    pub(crate) workflow_stages: Vec<String>,
    pub(crate) stage_ordinal: Option<u64>,
    pub(crate) aggregation_key: String,
    pub(crate) aggregation_batch: String,
    pub(crate) semantic_group_state: String,
    pub(crate) semantic_group_visible_total: u64,
    pub(crate) semantic_group_terminal_total: u64,
    pub(crate) page_selection: String,
    pub(crate) page_ranges: Vec<(u32, u32)>,
    pub(crate) document_page_count: Option<u32>,
    pub(crate) read_windows: Vec<ReadWindowPayload>,
    pub(crate) output: Vec<String>,
    pub(crate) exit_code: Option<i64>,
    pub(crate) duration_ms: Option<u64>,
    pub(crate) terminal_status: String,
    pub(crate) environment: CommandEnvironmentPayload,
    pub(crate) error: String,
    pub(crate) error_summary: String,
    pub(crate) failure_class: String,
    pub(crate) failure_severity: String,
    pub(crate) retryable: bool,
    pub(crate) failure_group_id: String,
    pub(crate) transcript: String,
    pub(crate) transcript_complete: bool,
    pub(crate) transcript_ref: String,
    pub(crate) transcript_omitted_bytes: u64,
    pub(crate) metadata: Vec<(String, String)>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct CitationVerificationPayload {
    pub(crate) citation_ref: String,
    pub(crate) bibtex_key: String,
    pub(crate) claim: String,
    pub(crate) assessor: String,
    pub(crate) assessment: String,
    pub(crate) content_levels: Vec<String>,
    pub(crate) evidence_binding: String,
    pub(crate) passage_count: Option<u64>,
    pub(crate) assessment_ref: String,
    pub(crate) diagnostics: Vec<String>,
    pub(crate) guidance: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ReadWindowPayload {
    pub(crate) start_line: u64,
    pub(crate) end_line: u64,
    pub(crate) total_lines: Option<u64>,
    pub(crate) has_more: Option<bool>,
    pub(crate) next_offset: Option<u64>,
    pub(crate) exact: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct BatchCallPayload {
    pub(crate) call_id: String,
    pub(crate) tool_name: String,
    pub(crate) action: String,
    pub(crate) target: String,
    pub(crate) display_target: String,
    pub(crate) qualifiers: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct BatchOutcomePayload {
    pub(crate) batch_id: String,
    pub(crate) execution_state: String,
    pub(crate) reason_code: String,
    pub(crate) reason: String,
    pub(crate) display_reason: String,
    pub(crate) diagnostic: String,
    pub(crate) recovery: String,
    pub(crate) calls: Vec<BatchCallPayload>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct CommandEnvironmentPayload {
    pub(crate) provider: String,
    pub(crate) name: String,
    pub(crate) python: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct FileChangePayload {
    pub(crate) compact_preview: bool,
    pub(crate) cwd: String,
    pub(crate) changes: FileChanges,
    pub(crate) stdout: String,
    pub(crate) stderr: String,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct McpToolCallPayload {
    pub(crate) server: String,
    pub(crate) tool: String,
    pub(crate) arguments: Value,
    pub(crate) output: Vec<String>,
    pub(crate) is_error: bool,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) enum WebEvidence {
    #[default]
    Unknown,
    MetadataOnly,
    Sources,
    Excerpt,
    Abstract,
}

impl WebEvidence {
    fn parse(value: &Value) -> Self {
        match value.as_str() {
            Some("metadata_only") => Self::MetadataOnly,
            Some("sources") => Self::Sources,
            Some("excerpt") => Self::Excerpt,
            Some("abstract") => Self::Abstract,
            _ => Self::Unknown,
        }
    }

    pub(crate) fn label(&self) -> &'static str {
        match self {
            Self::Unknown => "Content availability not recorded",
            Self::MetadataOnly => "Page text not included in receipt",
            Self::Sources => "Source links included · page text not included in receipt",
            Self::Excerpt => "Page excerpt included in receipt",
            Self::Abstract => "Abstract included in receipt · full text not included",
        }
    }
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct WebSearchPayload {
    pub(crate) action: String,
    pub(crate) query: String,
    pub(crate) url: String,
    pub(crate) display_url: String,
    pub(crate) display_query: String,
    pub(crate) http_status: Option<u16>,
    pub(crate) failure_kind: String,
    pub(crate) retryable: bool,
    pub(crate) error: String,
    pub(crate) error_summary: String,
    pub(crate) transcript: String,
    pub(crate) evidence: WebEvidence,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, serde::Deserialize)]
#[serde(default)]
pub(crate) struct CitationCandidatePayload {
    pub(crate) title: String,
    pub(crate) doi: String,
    pub(crate) year: String,
    pub(crate) authors: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ImagePayload {
    pub(crate) path: String,
    pub(crate) prompt: String,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PlanPayload {
    pub(crate) explanation: String,
    pub(crate) plan: Vec<PlanStep>,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) enum PlanStepStatus {
    #[default]
    Pending,
    InProgress,
    Completed,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PlanStep {
    pub(crate) step: String,
    pub(crate) status: PlanStepStatus,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct BackgroundTerminalPayload {
    pub(crate) session_id: String,
    pub(crate) input: String,
    pub(crate) output: Vec<String>,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct SkillPayload {
    pub(crate) skill_name: String,
    pub(crate) purpose: String,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TextPayload {
    pub(crate) role: String,
    pub(crate) text: String,
    pub(crate) phase: String,
    pub(crate) title: String,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

/// Provider-authored reasoning has two independent channels.
///
/// `summary` is intended for normal user-facing progress. `content` is the
/// provider's raw reasoning stream and must remain unavailable unless the
/// explicit raw-reasoning setting is enabled. `text` and `transcript` are
/// diagnostic source fields; they do not create a provider summary.
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ReasoningPayload {
    pub(crate) summary: Vec<String>,
    pub(crate) content: Vec<String>,
    pub(crate) stage: String,
    pub(crate) attempt: i64,
    pub(crate) continuation_index: i64,
    pub(crate) component: String,
    pub(crate) operation: String,
    pub(crate) text: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TaskHeaderPayload {
    pub(crate) title: String,
    pub(crate) route_kind: String,
    pub(crate) route_label: String,
    pub(crate) route_source: String,
    pub(crate) requested_profile: String,
    pub(crate) resolved_profile: String,
    pub(crate) mode: String,
    pub(crate) task_family: String,
    pub(crate) signal_modality: String,
    pub(crate) modality_pending: bool,
    pub(crate) dataset: String,
    pub(crate) dataset_inferred: bool,
    pub(crate) sources: Vec<String>,
    pub(crate) objective: String,
    pub(crate) text: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct RouteDecisionPayload {
    pub(crate) route_kind: String,
    pub(crate) route_label: String,
    pub(crate) initial_stage_label: String,
    pub(crate) elapsed_ms: u64,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct HeartbeatPayload {
    pub(crate) title: String,
    pub(crate) request: String,
    pub(crate) request_id: String,
    pub(crate) sequence: u64,
    pub(crate) phase: String,
    pub(crate) emitted_at: String,
    pub(crate) provider: String,
    pub(crate) model: String,
    pub(crate) attempt_count: u64,
    pub(crate) elapsed_ms: u64,
    pub(crate) error: String,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ModelActivityPayload {
    pub(crate) label: String,
    pub(crate) elapsed_ms: u64,
    pub(crate) stage: String,
    pub(crate) attempt: i64,
    pub(crate) continuation_index: i64,
    pub(crate) turn_index: i64,
    pub(crate) component: String,
    pub(crate) operation: String,
    pub(crate) outcome: String,
    pub(crate) provider_request_id: String,
    pub(crate) request_ordinal: u64,
    pub(crate) attempt_count: u64,
    pub(crate) retry_count: u64,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct StagePayload {
    pub(crate) run_id: String,
    pub(crate) stage_instance_id: String,
    pub(crate) result_updated: bool,
    pub(crate) stage: String,
    pub(crate) stage_label: String,
    pub(crate) stage_ordinal: Option<i64>,
    pub(crate) attempt: i64,
    pub(crate) event: String,
    pub(crate) status: String,
    pub(crate) contract: String,
    pub(crate) expected_outputs: Vec<String>,
    pub(crate) decision: String,
    pub(crate) gate_decision: String,
    pub(crate) requested_workflow_action: String,
    pub(crate) resolved_workflow_action: String,
    pub(crate) workflow_action_reason: String,
    pub(crate) next_stage: String,
    pub(crate) reason: String,
    pub(crate) reason_code: String,
    pub(crate) reason_display: String,
    pub(crate) basis_review_record_id: String,
    pub(crate) basis_review_kind: String,
    pub(crate) basis_review_round: i64,
    pub(crate) active_finding_count: u64,
    pub(crate) residual_review_record_id: String,
    pub(crate) residual_finding_count: u64,
    pub(crate) carry_in_review_record_ids: Vec<String>,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TaskArtifactRef {
    pub(crate) path: String,
    pub(crate) display_path: String,
    pub(crate) internal_citation_count: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct DeliveryWarning {
    pub(crate) code: String,
    pub(crate) request_id: String,
    pub(crate) path: String,
    pub(crate) source_kind: String,
    pub(crate) source_tool: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct TaskOutcomePayload {
    pub(crate) request_index: u64,
    pub(crate) reply_observation: String,
    pub(crate) recorded_files: Vec<TaskArtifactRef>,
    pub(crate) turn_completion: Option<TurnCompletionReceipt>,
    pub(crate) task_id: String,
    pub(crate) delivery_status: String,
    pub(crate) delivery_class: String,
    pub(crate) outcome: String,
    pub(crate) outcome_class: String,
    pub(crate) task_success: bool,
    pub(crate) display_status: String,
    pub(crate) delivery_summary: String,
    pub(crate) raw_final_text: String,
    pub(crate) primary_artifacts: Vec<TaskArtifactRef>,
    pub(crate) caveats: Vec<String>,
    pub(crate) outcome_source: String,
    pub(crate) verification_status: String,
    pub(crate) status_conflicts: Vec<String>,
    pub(crate) runtime_rationale: Vec<String>,
    pub(crate) delivery_warnings: Vec<DeliveryWarning>,
    pub(crate) failure_domain: String,
    pub(crate) failure_code: String,
    pub(crate) failure_summary: String,
    pub(crate) failure_cause: String,
    pub(crate) failure_detail: String,
    pub(crate) failure_facts: Vec<(String, String)>,
    pub(crate) provider_activity_scope: String,
    pub(crate) provider_request_count: u64,
    pub(crate) provider_retry_count: u64,
    pub(crate) final_request_attempt_count: u64,
    pub(crate) final_request_retry_count: u64,
    pub(crate) recovery_actions: Vec<String>,
}

/// Bridge-owned lifecycle data, distinct from task/delivery quality labels.
#[derive(Clone, Debug, PartialEq, Eq, serde::Deserialize)]
pub(crate) struct TurnCompletionReceipt {
    pub(crate) turn_id: String,
    pub(crate) status: String,
    pub(crate) elapsed_ms: u64,
    pub(crate) terminal_detail: String,
    pub(crate) required_terminal_item_ids: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ActionRequiredPayload {
    pub(crate) title: String,
    pub(crate) reason_code: String,
    pub(crate) detail: String,
    pub(crate) run_dir: String,
    pub(crate) recoverable: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ReviewFindingPayload {
    pub(crate) severity: String,
    pub(crate) title: String,
    pub(crate) description: String,
    pub(crate) evidence_refs: Vec<String>,
    pub(crate) display_evidence_refs: Vec<String>,
    pub(crate) remediation: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ReviewCyclePayload {
    pub(crate) title: String,
    pub(crate) stage: String,
    pub(crate) stage_instance_id: String,
    pub(crate) attempt: i64,
    pub(crate) review_kind: String,
    pub(crate) round: i64,
    pub(crate) max_rounds: i64,
    pub(crate) phase: String,
    pub(crate) outcome: String,
    pub(crate) summary: String,
    pub(crate) feedback_format: String,
    pub(crate) findings: Vec<ReviewFindingPayload>,
    pub(crate) overall_assessment: String,
    pub(crate) review_record_id: String,
    pub(crate) feedback_content_hash: String,
    pub(crate) source_sequence: u64,
    pub(crate) review_target_label: String,
    pub(crate) review_target: String,
    pub(crate) gate_disposition: String,
    pub(crate) gate_decision: String,
    pub(crate) elapsed_ms: u64,
    pub(crate) terminal_source: String,
    pub(crate) failure_domain: String,
    pub(crate) failure_code: String,
    pub(crate) failure_cause: String,
    pub(crate) failure_detail: String,
    pub(crate) provider_request_count: u64,
    pub(crate) provider_retry_count: u64,
    pub(crate) final_request_attempt_count: u64,
    pub(crate) final_request_retry_count: u64,
    pub(crate) transcript: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ProtocolCorrectionPayload {
    pub(crate) attempt: i64,
    pub(crate) kind: String,
    pub(crate) summary: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ProtocolActivityPayload {
    pub(crate) stage: String,
    pub(crate) attempt: i64,
    pub(crate) component: String,
    pub(crate) operation: String,
    pub(crate) round_id: String,
    pub(crate) corrections: Vec<ProtocolCorrectionPayload>,
    pub(crate) status_code: String,
    pub(crate) required_actions: Vec<String>,
    pub(crate) outcome: String,
    pub(crate) summary: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct InteractionAnswerPayload {
    pub(crate) question_id: String,
    pub(crate) question: String,
    pub(crate) answer: String,
    pub(crate) note: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct InteractionResultPayload {
    pub(crate) interaction_id: String,
    pub(crate) interaction_kind: String,
    pub(crate) title: String,
    pub(crate) answers: Vec<InteractionAnswerPayload>,
    pub(crate) decision: String,
    pub(crate) request_summary: String,
    pub(crate) outcome: String,
    pub(crate) file_changes: Vec<PermissionFileChange>,
    pub(crate) detail: String,
    pub(crate) resume_hint: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PermissionSubjectPayload {
    pub(crate) kind: String,
    pub(crate) primary: String,
    pub(crate) secondary: String,
    pub(crate) qualifiers: Vec<String>,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct PermissionActivityPayload {
    pub(crate) interaction_id: String,
    pub(crate) interaction_kind: String,
    pub(crate) lifecycle: String,
    pub(crate) title: String,
    pub(crate) request_summary: String,
    pub(crate) decision: String,
    pub(crate) decision_scope: String,
    pub(crate) file_changes: Vec<PermissionFileChange>,
    pub(crate) subject: PermissionSubjectPayload,
    pub(crate) display_group_id: String,
    pub(crate) detail: String,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ArtifactReceiptPayload {
    pub(crate) display_path: String,
    pub(crate) path: String,
    pub(crate) line_count: u64,
    pub(crate) byte_count: u64,
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct ArtifactActivityPayload {
    pub(crate) artifact_kind: String,
    pub(crate) run_id: String,
    pub(crate) artifacts: Vec<ArtifactReceiptPayload>,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum DisplayItem {
    BatchOutcome(DisplayItemCommon, BatchOutcomePayload),
    CommandExecution(DisplayItemCommon, CommandExecutionPayload),
    FileChange(DisplayItemCommon, FileChangePayload),
    McpToolCall(DisplayItemCommon, McpToolCallPayload),
    WebSearch(DisplayItemCommon, WebSearchPayload),
    ImageView(DisplayItemCommon, ImagePayload),
    ImageGeneration(DisplayItemCommon, ImagePayload),
    PlanUpdate(DisplayItemCommon, PlanPayload),
    BackgroundTerminalInteraction(DisplayItemCommon, BackgroundTerminalPayload),
    SkillActivity(DisplayItemCommon, SkillPayload),
    AgentMessage(DisplayItemCommon, TextPayload),
    Reasoning(DisplayItemCommon, ReasoningPayload),
    ProposedPlan(DisplayItemCommon, TextPayload),
    ContextCompaction(DisplayItemCommon, TextPayload),
    SubAgentActivity(DisplayItemCommon, TextPayload),
    Stage(DisplayItemCommon, StagePayload),
    Review(DisplayItemCommon, TextPayload),
    ReviewCycle(DisplayItemCommon, ReviewCyclePayload),
    ProtocolActivity(DisplayItemCommon, ProtocolActivityPayload),
    InteractionResult(DisplayItemCommon, InteractionResultPayload),
    PermissionActivity(DisplayItemCommon, PermissionActivityPayload),
    ArtifactActivity(DisplayItemCommon, ArtifactActivityPayload),
    LlmRequestFailure(DisplayItemCommon, TextPayload),
    ModelActivity(DisplayItemCommon, ModelActivityPayload),
    Heartbeat(DisplayItemCommon, HeartbeatPayload),
    RouteDecision(DisplayItemCommon, RouteDecisionPayload),
    TaskHeader(DisplayItemCommon, TaskHeaderPayload),
    TaskOutcome(DisplayItemCommon, TaskOutcomePayload),
    ActionRequired(DisplayItemCommon, ActionRequiredPayload),
}

impl DisplayItem {
    pub(crate) fn parse_value(value: &Value) -> Option<Self> {
        let schema = text(value, "schema_version");
        if schema != DISPLAY_ITEM_SCHEMA {
            return None;
        }
        if text(value, "visibility") != "user" {
            return None;
        }
        let payload = value.get("payload")?.as_object()?;
        let payload_value = Value::Object(payload.clone());
        let common = DisplayItemCommon {
            audit_context: value.get("audit_context").and_then(AuditContext::parse),
            actor_label: text(&value["scope"], "actor_label"),
            item_id: required_text(value, "item_id")?,
            call_id: text(value, "call_id"),
            tool_name: text(value, "tool_name"),
            status: required_text(value, "status")?,
            action: text(value, "action"),
            raw_source: text(value, "raw_source"),
            projection_phase: text(value, "projection_phase"),
            receipt_state: text(value, "receipt_state"),
            not_run_reason: text(&payload_value, "not_run_reason"),
            evidence_revision: value
                .get("evidence_revision")
                .and_then(Value::as_u64)
                .unwrap_or(0),
            retry_group_id: text(value, "retry_group_id"),
            attempt_index: value
                .get("attempt_index")
                .and_then(Value::as_u64)
                .unwrap_or(0),
            correction_of: text(value, "correction_of"),
            activity_group: ActivityGroupMeta {
                aggregation_batch: text(&payload_value, "aggregation_batch"),
                semantic_group_state: text(&payload_value, "semantic_group_state"),
                semantic_group_visible_total: payload
                    .get("semantic_group_visible_total")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
                semantic_group_terminal_total: payload
                    .get("semantic_group_terminal_total")
                    .and_then(Value::as_u64)
                    .unwrap_or(0),
            },
        };
        match text(value, "kind").as_str() {
            "batch_outcome" => Some(Self::BatchOutcome(
                common,
                BatchOutcomePayload {
                    batch_id: text(&payload_value, "batch_id"),
                    execution_state: text(&payload_value, "execution_state"),
                    reason_code: text(&payload_value, "reason_code"),
                    reason: text(&payload_value, "reason"),
                    display_reason: text(&payload_value, "display_reason"),
                    diagnostic: text(&payload_value, "diagnostic"),
                    recovery: text(&payload_value, "recovery"),
                    calls: batch_calls(&payload_value),
                },
            )),
            "command_execution" => Some(Self::CommandExecution(
                common,
                CommandExecutionPayload {
                    command: text(&payload_value, "command"),
                    requested_command: text(&payload_value, "requested_command"),
                    effective_command: text(&payload_value, "effective_command"),
                    cwd: text(&payload_value, "cwd"),
                    target: text(&payload_value, "target"),
                    display_target: text(&payload_value, "display_target"),
                    result_state: text(&payload_value, "result_state"),
                    citation_verification: citation_verification_payload(&payload_value),
                    requested_identifier: text(&payload_value, "requested_identifier"),
                    resolved_identifier: text(&payload_value, "resolved_identifier"),
                    requested_author: text(&payload_value, "requested_author"),
                    requested_title: text(&payload_value, "requested_title"),
                    requested_year: text(&payload_value, "requested_year"),
                    citation_candidates: payload
                        .get("citation_candidates")
                        .and_then(Value::as_array)
                        .map(|candidates| {
                            candidates
                                .iter()
                                .take(5)
                                .filter_map(|candidate| {
                                    serde_json::from_value(candidate.clone()).ok()
                                })
                                .collect()
                        })
                        .unwrap_or_default(),
                    citation_guidance: text(&payload_value, "citation_guidance"),
                    resolved_title: text(&payload_value, "resolved_title"),
                    resolved_authors: raw_string_list(&payload_value, "resolved_authors"),
                    resolved_year: text(&payload_value, "resolved_year"),
                    content_access_status: text(&payload_value, "content_access_status"),
                    identity_conflicts: raw_string_list(&payload_value, "identity_conflicts"),
                    diagnostics: raw_string_list(&payload_value, "diagnostics"),
                    path_exists: payload.get("path_exists").and_then(Value::as_bool),
                    query: text(&payload_value, "query"),
                    workflow_stages: raw_string_list(&payload_value, "workflow_stages"),
                    stage_ordinal: payload
                        .get("stage_ordinal")
                        .and_then(Value::as_u64)
                        .filter(|n| *n > 0),
                    aggregation_key: text(&payload_value, "aggregation_key"),
                    aggregation_batch: text(&payload_value, "aggregation_batch"),
                    semantic_group_state: text(&payload_value, "semantic_group_state"),
                    semantic_group_visible_total: payload
                        .get("semantic_group_visible_total")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    semantic_group_terminal_total: payload
                        .get("semantic_group_terminal_total")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    page_selection: text(&payload_value, "page_selection"),
                    page_ranges: page_range_pairs(&payload_value),
                    document_page_count: payload
                        .get("document_page_count")
                        .and_then(Value::as_u64)
                        .and_then(|value| u32::try_from(value).ok()),
                    read_windows: read_windows(&payload_value),
                    output: raw_string_list(&payload_value, "output"),
                    exit_code: payload.get("exit_code").and_then(Value::as_i64),
                    duration_ms: payload.get("duration_ms").and_then(Value::as_u64),
                    terminal_status: text(&payload_value, "terminal_status"),
                    environment: command_environment_payload(&payload_value),
                    error: text(&payload_value, "error"),
                    error_summary: text(&payload_value, "error_summary"),
                    failure_class: text(&payload_value, "failure_class"),
                    failure_severity: text(&payload_value, "failure_severity"),
                    retryable: payload
                        .get("retryable")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    failure_group_id: text(&payload_value, "failure_group_id"),
                    transcript: raw_text(&payload_value, "transcript"),
                    transcript_complete: payload
                        .get("transcript_complete")
                        .and_then(Value::as_bool)
                        .unwrap_or_else(|| {
                            !raw_text(&payload_value, "transcript").contains("...[truncated]...")
                        }),
                    transcript_ref: text(&payload_value, "transcript_ref"),
                    transcript_omitted_bytes: payload
                        .get("transcript_omitted_bytes")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    metadata: string_pairs(&payload_value, "metadata"),
                },
            )),
            "file_change" => Some(Self::FileChange(
                common,
                FileChangePayload {
                    compact_preview: payload_value["compact_preview"].as_bool().unwrap_or(false),
                    cwd: text(&payload_value, "cwd"),
                    changes: file_changes(&payload_value),
                    stdout: raw_text(&payload_value, "stdout"),
                    stderr: raw_text(&payload_value, "stderr"),
                    error: text(&payload_value, "error"),
                    transcript: raw_text(&payload_value, "transcript"),
                },
            )),
            "mcp_tool_call" => Some(Self::McpToolCall(
                common,
                McpToolCallPayload {
                    server: text(&payload_value, "server"),
                    tool: text(&payload_value, "tool"),
                    arguments: payload.get("arguments").cloned().unwrap_or(Value::Null),
                    output: raw_string_list(&payload_value, "output"),
                    is_error: payload
                        .get("is_error")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    error: text(&payload_value, "error"),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "web_search" => {
                let web_action = payload.get("web_action").unwrap_or(&Value::Null);
                Some(Self::WebSearch(
                    common,
                    WebSearchPayload {
                        evidence: WebEvidence::parse(&payload_value["evidence"]["availability"]),
                        action: text(web_action, "type"),
                        query: text(&payload_value, "query"),
                        url: text(web_action, "url"),
                        display_url: text(&payload_value, "display_url"),
                        display_query: text(&payload_value, "display_query"),
                        http_status: payload
                            .get("http_status")
                            .and_then(Value::as_u64)
                            .and_then(|value| u16::try_from(value).ok()),
                        failure_kind: text(&payload_value, "failure_kind"),
                        retryable: payload
                            .get("retryable")
                            .and_then(Value::as_bool)
                            .unwrap_or(false),
                        error: text(&payload_value, "error"),
                        error_summary: text(&payload_value, "error_summary"),
                        transcript: text(&payload_value, "transcript"),
                    },
                ))
            }
            "image_view" => Some(Self::ImageView(common, image_payload(&payload_value))),
            "image_generation" => {
                Some(Self::ImageGeneration(common, image_payload(&payload_value)))
            }
            "plan_update" => Some(Self::PlanUpdate(
                common,
                PlanPayload {
                    explanation: text(&payload_value, "explanation"),
                    plan: plan_pairs(&payload_value),
                    error: text(&payload_value, "error"),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "background_terminal_interaction" => Some(Self::BackgroundTerminalInteraction(
                common,
                BackgroundTerminalPayload {
                    session_id: text(&payload_value, "session_id"),
                    input: text(&payload_value, "input"),
                    output: raw_string_list(&payload_value, "output"),
                    error: text(&payload_value, "error"),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "skill_activity" => Some(Self::SkillActivity(
                common,
                SkillPayload {
                    skill_name: text(&payload_value, "skill_name"),
                    purpose: text(&payload_value, "purpose"),
                    error: text(&payload_value, "error"),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "agent_message" => Some(Self::AgentMessage(common, text_payload(&payload_value))),
            "reasoning" => Some(Self::Reasoning(common, reasoning_payload(&payload_value))),
            "proposed_plan" => Some(Self::ProposedPlan(common, text_payload(&payload_value))),
            "context_compaction" => Some(Self::ContextCompaction(
                common,
                text_payload(&payload_value),
            )),
            "sub_agent_activity" => {
                Some(Self::SubAgentActivity(common, text_payload(&payload_value)))
            }
            "stage" => Some(Self::Stage(
                common,
                StagePayload {
                    run_id: text(&payload_value, "run_id"),
                    stage_instance_id: text(&payload_value, "stage_instance_id"),
                    result_updated: payload
                        .get("result_updated")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    stage: text(&payload_value, "stage"),
                    stage_label: text(&payload_value, "stage_label"),
                    stage_ordinal: payload.get("stage_ordinal").and_then(Value::as_i64),
                    attempt: payload.get("attempt").and_then(Value::as_i64).unwrap_or(0),
                    event: text(&payload_value, "event"),
                    status: text(&payload_value, "status"),
                    contract: text(&payload_value, "contract"),
                    expected_outputs: string_list(&payload_value, "expected_outputs"),
                    decision: text(&payload_value, "decision"),
                    gate_decision: text(&payload_value, "gate_decision"),
                    requested_workflow_action: text(&payload_value, "requested_workflow_action"),
                    resolved_workflow_action: text(&payload_value, "resolved_workflow_action"),
                    workflow_action_reason: text(&payload_value, "workflow_action_reason"),
                    next_stage: text(&payload_value, "next_stage"),
                    reason: text(&payload_value, "reason"),
                    reason_code: text(&payload_value, "reason_code"),
                    reason_display: text(&payload_value, "reason_display"),
                    basis_review_record_id: text(&payload_value, "basis_review_record_id"),
                    basis_review_kind: text(&payload_value, "basis_review_kind"),
                    basis_review_round: payload
                        .get("basis_review_round")
                        .and_then(Value::as_i64)
                        .unwrap_or(0),
                    active_finding_count: payload
                        .get("active_finding_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    residual_review_record_id: text(&payload_value, "residual_review_record_id"),
                    residual_finding_count: payload
                        .get("residual_finding_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    carry_in_review_record_ids: string_list(
                        &payload_value,
                        "carry_in_review_record_ids",
                    ),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "review" => Some(Self::Review(common, text_payload(&payload_value))),
            "review_cycle" => Some(Self::ReviewCycle(
                common,
                ReviewCyclePayload {
                    title: text(&payload_value, "title"),
                    stage: text(&payload_value, "stage"),
                    stage_instance_id: text(&payload_value, "stage_instance_id"),
                    attempt: payload.get("attempt").and_then(Value::as_i64).unwrap_or(0),
                    review_kind: text(&payload_value, "review_kind"),
                    round: payload.get("round").and_then(Value::as_i64).unwrap_or(0),
                    max_rounds: payload
                        .get("max_rounds")
                        .and_then(Value::as_i64)
                        .unwrap_or(0),
                    phase: text(&payload_value, "phase"),
                    outcome: text(&payload_value, "outcome"),
                    summary: raw_text(&payload_value, "summary"),
                    feedback_format: text(&payload_value, "feedback_format"),
                    findings: review_findings(&payload_value),
                    overall_assessment: raw_text(&payload_value, "overall_assessment"),
                    review_record_id: text(&payload_value, "review_record_id"),
                    feedback_content_hash: text(&payload_value, "feedback_content_hash"),
                    source_sequence: payload
                        .get("source_sequence")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    review_target_label: text(&payload_value, "review_target_label"),
                    review_target: text(&payload_value, "review_target"),
                    gate_disposition: text(&payload_value, "gate_disposition"),
                    gate_decision: text(&payload_value, "gate_decision"),
                    elapsed_ms: payload
                        .get("elapsed_ms")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    terminal_source: text(&payload_value, "terminal_source"),
                    failure_domain: text(&payload_value, "failure_domain"),
                    failure_code: text(&payload_value, "failure_code"),
                    failure_cause: text(&payload_value, "failure_cause"),
                    failure_detail: text(&payload_value, "failure_detail"),
                    provider_request_count: payload
                        .get("provider_request_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    provider_retry_count: payload
                        .get("provider_retry_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    final_request_attempt_count: payload
                        .get("final_request_attempt_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    final_request_retry_count: payload
                        .get("final_request_retry_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    transcript: raw_text(&payload_value, "transcript"),
                },
            )),
            "protocol_activity" => Some(Self::ProtocolActivity(
                common,
                ProtocolActivityPayload {
                    stage: text(&payload_value, "stage"),
                    attempt: payload.get("attempt").and_then(Value::as_i64).unwrap_or(0),
                    component: text(&payload_value, "component"),
                    operation: text(&payload_value, "operation"),
                    round_id: text(&payload_value, "round_id"),
                    corrections: protocol_corrections(&payload_value),
                    status_code: text(&payload_value, "status_code"),
                    required_actions: string_list(&payload_value, "required_actions"),
                    outcome: text(&payload_value, "outcome"),
                    summary: text(&payload_value, "summary"),
                },
            )),
            "interaction_result" => Some(Self::InteractionResult(
                common,
                InteractionResultPayload {
                    interaction_id: text(&payload_value, "interaction_id"),
                    interaction_kind: text(&payload_value, "interaction_kind"),
                    title: text(&payload_value, "title"),
                    answers: interaction_answers(&payload_value),
                    decision: text(&payload_value, "decision"),
                    request_summary: text(&payload_value, "request_summary"),
                    outcome: text(&payload_value, "outcome"),
                    file_changes: permission_file_changes(&payload_value),
                    detail: text(&payload_value, "detail"),
                    resume_hint: text(&payload_value, "resume_hint"),
                },
            )),
            "permission_activity" => Some(Self::PermissionActivity(
                common,
                PermissionActivityPayload {
                    interaction_id: text(&payload_value, "interaction_id"),
                    interaction_kind: text(&payload_value, "interaction_kind"),
                    lifecycle: text(&payload_value, "lifecycle"),
                    title: text(&payload_value, "title"),
                    request_summary: text(&payload_value, "request_summary"),
                    decision: text(&payload_value, "decision"),
                    decision_scope: text(&payload_value, "decision_scope"),
                    file_changes: permission_file_changes(&payload_value),
                    subject: PermissionSubjectPayload {
                        kind: nested_text(&payload_value, "subject", "kind"),
                        primary: nested_text(&payload_value, "subject", "primary"),
                        secondary: nested_text(&payload_value, "subject", "secondary"),
                        qualifiers: nested_string_list(&payload_value, "subject", "qualifiers"),
                    },
                    display_group_id: text(&payload_value, "display_group_id"),
                    detail: text(&payload_value, "detail"),
                },
            )),
            "artifact_activity" => Some(Self::ArtifactActivity(
                common,
                ArtifactActivityPayload {
                    artifact_kind: text(&payload_value, "artifact_kind"),
                    run_id: text(&payload_value, "run_id"),
                    artifacts: artifact_receipts(&payload_value),
                },
            )),
            "llm_request_failure" => Some(Self::LlmRequestFailure(
                common,
                text_payload(&payload_value),
            )),
            "model_activity" => Some(Self::ModelActivity(
                common,
                ModelActivityPayload {
                    label: text(&payload_value, "label"),
                    elapsed_ms: payload
                        .get("elapsed_ms")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    stage: text(&payload_value, "stage"),
                    attempt: payload.get("attempt").and_then(Value::as_i64).unwrap_or(0),
                    continuation_index: payload
                        .get("continuation_index")
                        .and_then(Value::as_i64)
                        .unwrap_or(0),
                    turn_index: payload
                        .get("turn_index")
                        .and_then(Value::as_i64)
                        .unwrap_or(0),
                    component: text(&payload_value, "component"),
                    operation: text(&payload_value, "operation"),
                    outcome: text(&payload_value, "outcome"),
                    provider_request_id: text(&payload_value, "provider_request_id"),
                    request_ordinal: payload
                        .get("request_ordinal")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    attempt_count: payload
                        .get("attempt_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    retry_count: payload
                        .get("retry_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "heartbeat" => Some(Self::Heartbeat(common, heartbeat_payload(&payload_value))),
            "route_decision" => Some(Self::RouteDecision(
                common,
                RouteDecisionPayload {
                    route_kind: text(&payload_value, "route_kind"),
                    route_label: text(&payload_value, "route_label"),
                    initial_stage_label: text(&payload_value, "initial_stage_label"),
                    elapsed_ms: payload
                        .get("elapsed_ms")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "task_header" => Some(Self::TaskHeader(
                common,
                TaskHeaderPayload {
                    title: text(&payload_value, "title"),
                    route_kind: text(&payload_value, "route_kind"),
                    route_label: text(&payload_value, "route_label"),
                    route_source: text(&payload_value, "route_source"),
                    requested_profile: text(&payload_value, "requested_profile"),
                    resolved_profile: text(&payload_value, "resolved_profile"),
                    mode: text(&payload_value, "mode"),
                    task_family: text(&payload_value, "task_family"),
                    signal_modality: text(&payload_value, "signal_modality"),
                    modality_pending: payload
                        .get("modality_pending")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    dataset: text(&payload_value, "dataset"),
                    dataset_inferred: payload
                        .get("dataset_inferred")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    sources: string_list(&payload_value, "sources"),
                    objective: text(&payload_value, "objective"),
                    text: text(&payload_value, "text"),
                    transcript: text(&payload_value, "transcript"),
                },
            )),
            "task_outcome" => Some(Self::TaskOutcome(
                common,
                TaskOutcomePayload {
                    request_index: payload
                        .get("request_index")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    reply_observation: text(&payload_value, "reply_observation"),
                    recorded_files: task_artifact_refs(&payload_value, "recorded_files"),
                    turn_completion: payload
                        .get("turn_completion")
                        .and_then(|value| serde_json::from_value(value.clone()).ok()),
                    task_id: text(&payload_value, "task_id"),
                    display_status: text(&payload_value, "display_status"),
                    delivery_summary: text(&payload_value, "delivery_summary"),
                    raw_final_text: text(&payload_value, "raw_final_text"),
                    delivery_status: text(&payload_value, "delivery_status"),
                    delivery_class: text(&payload_value, "delivery_class"),
                    outcome: text(&payload_value, "outcome"),
                    outcome_class: text(&payload_value, "outcome_class"),
                    task_success: payload
                        .get("task_success")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                    primary_artifacts: task_artifact_refs(&payload_value, "primary_artifacts"),
                    caveats: string_list(&payload_value, "caveats"),
                    outcome_source: text(&payload_value, "outcome_source"),
                    verification_status: text(&payload_value, "verification_status"),
                    status_conflicts: string_list(&payload_value, "status_conflicts"),
                    runtime_rationale: string_list(&payload_value, "runtime_rationale"),
                    delivery_warnings: delivery_warnings(&payload_value),
                    failure_domain: text(&payload_value, "failure_domain"),
                    failure_code: text(&payload_value, "failure_code"),
                    failure_summary: text(&payload_value, "failure_summary"),
                    failure_cause: text(&payload_value, "failure_cause"),
                    failure_detail: text(&payload_value, "failure_detail"),
                    failure_facts: payload
                        .get("failure_facts")
                        .and_then(|value| serde_json::from_value(value.clone()).ok())
                        .unwrap_or_default(),
                    provider_activity_scope: text(&payload_value, "provider_activity_scope"),
                    provider_request_count: payload
                        .get("provider_request_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    provider_retry_count: payload
                        .get("provider_retry_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    final_request_attempt_count: payload
                        .get("final_request_attempt_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    final_request_retry_count: payload
                        .get("final_request_retry_count")
                        .and_then(Value::as_u64)
                        .unwrap_or(0),
                    recovery_actions: string_list(&payload_value, "recovery_actions"),
                },
            )),
            "action_required" => Some(Self::ActionRequired(
                common,
                ActionRequiredPayload {
                    title: text(&payload_value, "title"),
                    reason_code: text(&payload_value, "reason_code"),
                    detail: text(&payload_value, "detail"),
                    run_dir: text(&payload_value, "run_dir"),
                    recoverable: payload
                        .get("recoverable")
                        .and_then(Value::as_bool)
                        .unwrap_or(false),
                },
            )),
            _ => None,
        }
    }

    pub(crate) fn common(&self) -> &DisplayItemCommon {
        match self {
            Self::BatchOutcome(common, _)
            | Self::CommandExecution(common, _)
            | Self::FileChange(common, _)
            | Self::McpToolCall(common, _)
            | Self::WebSearch(common, _)
            | Self::ImageView(common, _)
            | Self::ImageGeneration(common, _)
            | Self::PlanUpdate(common, _)
            | Self::BackgroundTerminalInteraction(common, _)
            | Self::SkillActivity(common, _)
            | Self::AgentMessage(common, _)
            | Self::Reasoning(common, _)
            | Self::ProposedPlan(common, _)
            | Self::ContextCompaction(common, _)
            | Self::SubAgentActivity(common, _)
            | Self::Stage(common, _)
            | Self::Review(common, _)
            | Self::ReviewCycle(common, _)
            | Self::ProtocolActivity(common, _)
            | Self::InteractionResult(common, _)
            | Self::PermissionActivity(common, _)
            | Self::ArtifactActivity(common, _)
            | Self::LlmRequestFailure(common, _)
            | Self::ModelActivity(common, _)
            | Self::Heartbeat(common, _)
            | Self::RouteDecision(common, _)
            | Self::TaskHeader(common, _) => common,
            Self::TaskOutcome(common, _) | Self::ActionRequired(common, _) => common,
        }
    }
}

fn protocol_corrections(value: &Value) -> Vec<ProtocolCorrectionPayload> {
    value
        .get("corrections")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|item| item.is_object())
        .map(|item| ProtocolCorrectionPayload {
            attempt: item.get("attempt").and_then(Value::as_i64).unwrap_or(0),
            kind: text(item, "kind"),
            summary: text(item, "summary"),
        })
        .collect()
}

fn interaction_answers(value: &Value) -> Vec<InteractionAnswerPayload> {
    value
        .get("answers")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|item| item.is_object())
        .map(|item| InteractionAnswerPayload {
            question_id: text(item, "question_id"),
            question: text(item, "question"),
            answer: text(item, "answer"),
            note: text(item, "note"),
        })
        .collect()
}

fn artifact_receipts(value: &Value) -> Vec<ArtifactReceiptPayload> {
    value
        .get("artifacts")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|item| item.is_object())
        .map(|item| ArtifactReceiptPayload {
            display_path: text(item, "display_path"),
            path: text(item, "path"),
            line_count: item.get("line_count").and_then(Value::as_u64).unwrap_or(0),
            byte_count: item.get("byte_count").and_then(Value::as_u64).unwrap_or(0),
        })
        .collect()
}

pub(crate) fn canonical_stage_label(
    stage_label: &str,
    stage_ordinal: Option<i64>,
    stage: &str,
) -> String {
    let label = if stage_label.trim().is_empty() {
        stage.trim()
    } else {
        stage_label.trim()
    };
    let Some(ordinal) = stage_ordinal.filter(|value| *value > 0) else {
        return label.to_string();
    };
    for separator in [':', '·'] {
        let Some((prefix, remainder)) = label.split_once(separator) else {
            continue;
        };
        if prefix.trim().parse::<i64>().ok() == Some(ordinal) {
            let remainder = remainder.trim();
            return if remainder.is_empty() {
                stage.trim().to_string()
            } else {
                remainder.to_string()
            };
        }
    }
    label.to_string()
}

fn image_payload(value: &Value) -> ImagePayload {
    let path = text(value, "path");
    ImagePayload {
        path: if path.is_empty() {
            text(value, "saved_path")
        } else {
            path
        },
        prompt: text(value, "prompt"),
        error: text(value, "error"),
        transcript: text(value, "transcript"),
    }
}

fn text_payload(value: &Value) -> TextPayload {
    TextPayload {
        role: text(value, "role"),
        text: raw_text(value, "text"),
        phase: text(value, "phase"),
        title: text(value, "title"),
        error: text(value, "error"),
        transcript: text(value, "transcript"),
    }
}

fn reasoning_payload(value: &Value) -> ReasoningPayload {
    let text = raw_text(value, "text");
    let transcript = raw_text(value, "transcript");
    let summary = raw_string_list(value, "summary");
    ReasoningPayload {
        summary,
        content: raw_string_list(value, "content"),
        stage: raw_text(value, "stage").trim().to_string(),
        attempt: value.get("attempt").and_then(Value::as_i64).unwrap_or(0),
        continuation_index: value
            .get("continuation_index")
            .and_then(Value::as_i64)
            .unwrap_or(0),
        component: raw_text(value, "component").trim().to_string(),
        operation: raw_text(value, "operation").trim().to_string(),
        text,
        transcript,
    }
}

fn heartbeat_payload(value: &Value) -> HeartbeatPayload {
    let request = text(value, "request");
    let elapsed_ms = value.get("elapsed_ms").and_then(Value::as_u64);
    HeartbeatPayload {
        title: text(value, "title"),
        request,
        request_id: text(value, "request_id"),
        sequence: value.get("sequence").and_then(Value::as_u64).unwrap_or(0),
        phase: text(value, "phase"),
        emitted_at: text(value, "emitted_at"),
        provider: text(value, "provider"),
        model: text(value, "model"),
        attempt_count: value
            .get("attempt_count")
            .and_then(Value::as_u64)
            .unwrap_or(0),
        elapsed_ms: elapsed_ms.unwrap_or(0),
        error: text(value, "error"),
        transcript: text(value, "transcript"),
    }
}

fn required_text(value: &Value, key: &str) -> Option<String> {
    let value = text(value, key);
    (!value.is_empty()).then_some(value)
}

fn text(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .trim()
        .to_string()
}

fn nested_text(value: &Value, object_key: &str, key: &str) -> String {
    value
        .get(object_key)
        .map(|nested| text(nested, key))
        .unwrap_or_default()
}

fn raw_text(value: &Value, key: &str) -> String {
    value
        .get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

fn raw_string_list(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn string_list(value: &Value, key: &str) -> Vec<String> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(str::trim)
                .filter(|item| !item.is_empty())
                .map(str::to_string)
                .collect()
        })
        .unwrap_or_default()
}

fn nested_string_list(value: &Value, object_key: &str, key: &str) -> Vec<String> {
    value
        .get(object_key)
        .map(|nested| string_list(nested, key))
        .unwrap_or_default()
}

fn review_findings(value: &Value) -> Vec<ReviewFindingPayload> {
    value
        .get("findings")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let severity = text(item, "severity");
                    let title = text(item, "title");
                    if severity.is_empty() || title.is_empty() {
                        return None;
                    }
                    Some(ReviewFindingPayload {
                        severity,
                        title,
                        description: raw_text(item, "description"),
                        evidence_refs: string_list(item, "evidence_refs"),
                        display_evidence_refs: string_list(item, "display_evidence_refs"),
                        remediation: raw_text(item, "remediation"),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn task_artifact_refs(value: &Value, key: &str) -> Vec<TaskArtifactRef> {
    value
        .get(key)
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    if let Some(path) = item.as_str() {
                        let path = path.trim();
                        return (!path.is_empty()).then(|| TaskArtifactRef {
                            path: path.to_string(),
                            display_path: path.to_string(),
                            internal_citation_count: 0,
                        });
                    }
                    let object = item.as_object()?;
                    let path = object
                        .get("path")
                        .and_then(Value::as_str)
                        .unwrap_or_default()
                        .trim();
                    if path.is_empty() {
                        return None;
                    }
                    let display_path = object
                        .get("display_path")
                        .and_then(Value::as_str)
                        .unwrap_or(path)
                        .trim();
                    Some(TaskArtifactRef {
                        path: path.to_string(),
                        internal_citation_count: object
                            .get("internal_citation_count")
                            .and_then(Value::as_u64)
                            .unwrap_or(0),
                        display_path: if display_path.is_empty() {
                            path.to_string()
                        } else {
                            display_path.to_string()
                        },
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn delivery_warnings(value: &Value) -> Vec<DeliveryWarning> {
    value
        .get("delivery_warnings")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|item| {
            let code = text(item, "code");
            (!code.trim().is_empty()).then(|| DeliveryWarning {
                code,
                request_id: text(item, "request_id"),
                path: text(item, "path"),
                source_kind: text(item, "source_kind"),
                source_tool: text(item, "source_tool"),
            })
        })
        .collect()
}

fn string_pairs(value: &Value, key: &str) -> Vec<(String, String)> {
    value
        .get(key)
        .and_then(Value::as_object)
        .map(|object| {
            object
                .iter()
                .filter_map(|(key, value)| {
                    value
                        .as_str()
                        .map(str::trim)
                        .filter(|value| !value.is_empty())
                        .map(|value| (key.clone(), value.to_string()))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn citation_verification_payload(value: &Value) -> CitationVerificationPayload {
    let verification = value.get("citation_verification").unwrap_or(&Value::Null);
    CitationVerificationPayload {
        citation_ref: text(verification, "citation_ref"),
        bibtex_key: text(verification, "bibtex_key"),
        claim: text(verification, "claim"),
        assessor: text(verification, "assessor"),
        assessment: text(verification, "assessment"),
        content_levels: raw_string_list(verification, "content_levels"),
        evidence_binding: text(verification, "evidence_binding"),
        passage_count: verification.get("passage_count").and_then(Value::as_u64),
        assessment_ref: text(verification, "assessment_ref"),
        diagnostics: raw_string_list(verification, "diagnostics"),
        guidance: text(verification, "guidance"),
    }
}

fn command_environment_payload(value: &Value) -> CommandEnvironmentPayload {
    let environment = value.get("environment").unwrap_or(&Value::Null);
    CommandEnvironmentPayload {
        provider: text(environment, "provider"),
        name: text(environment, "name"),
        python: text(environment, "python"),
    }
}

fn file_changes(value: &Value) -> FileChanges {
    let mut changes = FileChanges::default();
    let Some(items) = value.get("changes").and_then(Value::as_array) else {
        return changes;
    };
    for item in items {
        let path = text(item, "path");
        if path.is_empty() {
            continue;
        }
        let kind = text(item, "kind");
        let change = match kind.as_str() {
            "add" => FileChange::Add {
                content: raw_text(item, "content"),
            },
            "delete" => FileChange::Delete {
                content: raw_text(item, "content"),
            },
            "update" => FileChange::Update {
                unified_diff: raw_text(item, "unified_diff"),
                move_path: nonempty_text(item, "move_path"),
            },
            "move" => FileChange::Move {
                unified_diff: raw_text(item, "unified_diff"),
                move_path: text(item, "move_path"),
            },
            "directory" => FileChange::Directory,
            "noop" => FileChange::Noop,
            "pending" => FileChange::Pending {
                move_path: nonempty_text(item, "move_path"),
            },
            "unknown" => FileChange::Unknown,
            _ => continue,
        };
        changes.push(FileChangeEntry {
            path,
            change,
            operation: nonempty_text(item, "operation").unwrap_or(kind),
            patch_excerpt: raw_text(item, "patch_excerpt"),
            added_lines: optional_usize(item, "added_lines"),
            removed_lines: optional_usize(item, "removed_lines"),
            reason: text(item, "reason"),
            body_complete: item
                .get("body_complete")
                .and_then(Value::as_bool)
                .unwrap_or(true),
            body_omitted_bytes: optional_usize(item, "body_omitted_bytes").unwrap_or(0),
            body_ref: text(item, "body_ref"),
            overwrote_destination: item
                .get("overwrote_destination")
                .and_then(Value::as_bool)
                .unwrap_or(false),
        });
    }
    changes
}

fn permission_file_changes(value: &Value) -> Vec<PermissionFileChange> {
    value
        .get("file_changes")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let path = text(item, "path");
                    if path.trim().is_empty() {
                        return None;
                    }
                    let change_kind =
                        nonempty_text(item, "change_kind").unwrap_or_else(|| text(item, "kind"));
                    Some(PermissionFileChange {
                        path,
                        destination: text(item, "destination"),
                        change_kind,
                        added_lines: item.get("added_lines").and_then(Value::as_u64),
                        removed_lines: item.get("removed_lines").and_then(Value::as_u64),
                        size_bytes: item.get("size_bytes").and_then(Value::as_u64),
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn optional_usize(value: &Value, key: &str) -> Option<usize> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .and_then(|value| usize::try_from(value).ok())
}

fn nonempty_text(value: &Value, key: &str) -> Option<String> {
    let value = text(value, key);
    (!value.is_empty()).then_some(value)
}

fn plan_pairs(value: &Value) -> Vec<PlanStep> {
    value
        .get("plan")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(|item| {
                    let step = text(item, "step");
                    (!step.is_empty()).then(|| PlanStep {
                        step,
                        status: match text(item, "status").as_str() {
                            "completed" => PlanStepStatus::Completed,
                            "in_progress" => PlanStepStatus::InProgress,
                            _ => PlanStepStatus::Pending,
                        },
                    })
                })
                .collect()
        })
        .unwrap_or_default()
}

fn page_range_pairs(value: &Value) -> Vec<(u32, u32)> {
    value
        .get("page_ranges")
        .and_then(Value::as_array)
        .map(|ranges| {
            ranges
                .iter()
                .filter_map(|range| {
                    let values = range.as_array()?;
                    let start = u32::try_from(values.first()?.as_u64()?).ok()?;
                    let end = u32::try_from(values.get(1)?.as_u64()?).ok()?;
                    (start > 0 && end >= start).then_some((start, end))
                })
                .collect()
        })
        .unwrap_or_default()
}

fn read_windows(value: &Value) -> Vec<ReadWindowPayload> {
    value
        .get("read_windows")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|window| {
            let start_line = window.get("start_line")?.as_u64()?;
            let end_line = window.get("end_line")?.as_u64()?;
            (start_line > 0 && end_line >= start_line).then(|| ReadWindowPayload {
                start_line,
                end_line,
                total_lines: window.get("total_lines").and_then(Value::as_u64),
                has_more: window.get("has_more").and_then(Value::as_bool),
                next_offset: window.get("next_offset").and_then(Value::as_u64),
                exact: window
                    .get("exact")
                    .and_then(Value::as_bool)
                    .unwrap_or(false),
            })
        })
        .collect()
}

fn batch_calls(value: &Value) -> Vec<BatchCallPayload> {
    value
        .get("calls")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(|call| {
            let call_id = text(call, "call_id");
            (!call_id.is_empty()).then(|| BatchCallPayload {
                call_id,
                tool_name: text(call, "tool_name"),
                action: text(call, "action"),
                target: text(call, "target"),
                display_target: text(call, "display_target"),
                qualifiers: string_list(call, "qualifiers"),
            })
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_structured_batch_outcome_details_and_legacy_defaults() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "batch_outcome",
            "item_id": "batch-outcome:batch-1:mixed",
            "call_id": "batch-outcome:batch-1:mixed",
            "tool_name": "tool_batch",
            "status": "completed",
            "action": "batch_outcome",
            "visibility": "user",
            "payload": {
                "batch_id": "batch-1",
                "execution_state": "not_executed",
                "reason_code": "permission_pause_tool_mixed_batch",
                "reason": "raw diagnostic",
                "display_reason": "No calls started.",
                "diagnostic": "raw diagnostic",
                "recovery": "Retry separately.",
                "calls": [{
                    "call_id": "call-1",
                    "tool_name": "file_reader",
                    "action": "read",
                    "target": "/workspace/review.md",
                    "display_target": "review.md",
                    "qualifiers": ["offset 35", "limit 80"]
                }]
            }
        });

        let DisplayItem::BatchOutcome(_, payload) =
            DisplayItem::parse_value(&value).expect("batch outcome")
        else {
            panic!("expected batch outcome");
        };
        assert_eq!(payload.display_reason, "No calls started.");
        assert_eq!(payload.diagnostic, "raw diagnostic");
        assert_eq!(payload.recovery, "Retry separately.");
        assert_eq!(payload.calls[0].display_target, "review.md");
        assert_eq!(payload.calls[0].qualifiers, ["offset 35", "limit 80"]);

        let mut legacy = value;
        let legacy_payload = legacy["payload"].as_object_mut().expect("payload object");
        legacy_payload.remove("display_reason");
        legacy_payload.remove("diagnostic");
        legacy_payload.remove("recovery");
        let legacy_call = legacy_payload["calls"][0]
            .as_object_mut()
            .expect("call object");
        legacy_call.remove("display_target");
        legacy_call.remove("qualifiers");
        let DisplayItem::BatchOutcome(_, legacy_payload) =
            DisplayItem::parse_value(&legacy).expect("legacy batch outcome")
        else {
            panic!("expected legacy batch outcome");
        };
        assert!(legacy_payload.display_reason.is_empty());
        assert!(legacy_payload.calls[0].display_target.is_empty());
        assert!(legacy_payload.calls[0].qualifiers.is_empty());
    }

    #[test]
    fn parses_v3_web_search_and_rejects_old_schema() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "web_search",
            "item_id": "web-1",
            "call_id": "web-1",
            "tool_name": "web_search",
            "status": "completed",
            "action": "search",
            "visibility": "user",
            "projection_phase": "terminal",
            "receipt_state": "authoritative",
            "evidence_revision": 3,
            "retry_group_id": "retry:web-1",
            "attempt_index": 2,
            "correction_of": "web-0",
            "payload": {
                "query": "shared latent representations",
                "web_action": {"type": "search", "query": "shared latent representations"}
            }
        });

        let parsed = DisplayItem::parse_value(&value).expect("v3 web item");
        let DisplayItem::WebSearch(common, payload) = parsed else {
            panic!("expected web search");
        };
        assert_eq!(common.status, "completed");
        assert_eq!(common.projection_phase, "terminal");
        assert_eq!(common.receipt_state, "authoritative");
        assert_eq!(common.evidence_revision, 3);
        assert_eq!(common.retry_group_id, "retry:web-1");
        assert_eq!(common.attempt_index, 2);
        assert_eq!(common.correction_of, "web-0");
        assert_eq!(payload.query, "shared latent representations");

        let mut old = value;
        old["schema_version"] = Value::String("ownpi.chat_replay_item.v2".to_string());
        assert!(DisplayItem::parse_value(&old).is_none());
    }

    #[test]
    fn parses_reasoning_summary_and_raw_content_without_conflation() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "reasoning",
            "item_id": "reason-1",
            "call_id": "reason-1",
            "tool_name": "reasoning",
            "status": "completed",
            "action": "reason",
            "visibility": "user",
            "payload": {
                "summary": ["Inspect", "Verify"],
                "content": ["provider-private reasoning"],
                "stage": "literature_grounding",
                "attempt": 2,
                "continuation_index": 3,
                "component": "core",
                "operation": "native_core_stage_round",
                "text": "Inspect\nVerify",
                "transcript": "Inspect\nVerify"
            }
        });

        let DisplayItem::Reasoning(_, payload) =
            DisplayItem::parse_value(&value).expect("reasoning item")
        else {
            panic!("expected reasoning");
        };
        assert_eq!(payload.summary, ["Inspect", "Verify"]);
        assert_eq!(payload.content, ["provider-private reasoning"]);
        assert_eq!(payload.stage, "literature_grounding");
        assert_eq!(payload.attempt, 2);
        assert_eq!(payload.continuation_index, 3);
        assert_eq!(payload.component, "core");
        assert_eq!(payload.operation, "native_core_stage_round");
        assert_eq!(payload.text, "Inspect\nVerify");
    }

    #[test]
    fn reasoning_text_does_not_create_a_provider_summary() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "reasoning",
            "item_id": "reason-legacy",
            "call_id": "reason-legacy",
            "tool_name": "reasoning_summary",
            "status": "completed",
            "action": "reason",
            "visibility": "user",
            "payload": {"text": "legacy summary"}
        });

        let DisplayItem::Reasoning(_, payload) =
            DisplayItem::parse_value(&value).expect("legacy reasoning item")
        else {
            panic!("expected reasoning");
        };
        assert!(payload.summary.is_empty());
        assert!(payload.content.is_empty());
    }

    #[test]
    fn preserves_complete_command_output_for_rendering_and_transcript() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "command_execution",
            "item_id": "cmd-1",
            "call_id": "cmd-1",
            "tool_name": "test_runner",
            "status": "failed",
            "action": "run",
            "visibility": "user",
            "payload": {
                "command": "pytest -q",
                "page_ranges": [[1, 3], [5, 5]],
                "output": ["1", "2", "3", "4", "5", "6"],
                "error": "command timed out"
            }
        });
        let DisplayItem::CommandExecution(_, payload) =
            DisplayItem::parse_value(&value).expect("command item")
        else {
            panic!("expected command execution");
        };
        assert_eq!(payload.output, ["1", "2", "3", "4", "5", "6"]);
        assert_eq!(payload.page_ranges, [(1, 3), (5, 5)]);
        assert_eq!(payload.error, "command timed out");
    }

    #[test]
    fn parses_structured_file_changes_without_losing_diff_lines() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "file_change",
            "item_id": "patch-1",
            "call_id": "patch-1",
            "tool_name": "patch_apply",
            "status": "completed",
            "action": "change",
            "visibility": "user",
            "payload": {
                "cwd": "/workspace",
                "changes": [
                    {
                        "path": "/workspace/added.py",
                        "kind": "add",
                        "content": "print('added')\n"
                    },
                    {
                        "path": "/workspace/edited.py",
                        "kind": "update",
                        "operation": "file_edit",
                        "unified_diff": "@@ -1 +1 @@\n-old\n+new\n",
                        "added_lines": 1,
                        "removed_lines": 1,
                        "body_complete": true
                    },
                    {
                        "path": "/workspace/old.py",
                        "kind": "move",
                        "operation": "rename",
                        "move_path": "/workspace/new.py",
                        "unified_diff": "",
                        "added_lines": 0,
                        "removed_lines": 0,
                        "body_complete": true
                    },
                    {
                        "path": "/workspace/edited.py",
                        "kind": "noop",
                        "operation": "file_write",
                        "reason": "content_unchanged",
                        "body_complete": true
                    }
                ]
            }
        });

        let DisplayItem::FileChange(_, payload) =
            DisplayItem::parse_value(&value).expect("file change item")
        else {
            panic!("expected file change");
        };
        assert_eq!(payload.cwd, "/workspace");
        assert_eq!(
            payload.changes.get("/workspace/added.py"),
            Some(&FileChange::Add {
                content: "print('added')\n".to_string()
            })
        );
        let entries = payload.changes.entries().collect::<Vec<_>>();
        assert_eq!(entries.len(), 4);
        assert_eq!(entries[1].operation, "file_edit");
        assert_eq!(entries[1].added_lines, Some(1));
        assert_eq!(entries[1].removed_lines, Some(1));
        assert_eq!(entries[2].operation, "rename");
        assert_eq!(
            entries[2].change,
            FileChange::Move {
                unified_diff: String::new(),
                move_path: "/workspace/new.py".to_string(),
            }
        );
        assert_eq!(entries[3].path, "/workspace/edited.py");
        assert_eq!(entries[3].change, FileChange::Noop);
        assert_eq!(entries[3].reason, "content_unchanged");
    }

    #[test]
    fn plan_status_is_typed_and_unknown_replay_status_is_pending() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "plan_update",
            "item_id": "plan-1",
            "call_id": "plan-1",
            "tool_name": "update_plan",
            "status": "completed",
            "action": "update",
            "visibility": "user",
            "payload": {
                "plan": [
                    {"step": "active", "status": "in_progress"},
                    {"step": "legacy", "status": "IN_PROGRESS"}
                ]
            }
        });

        let DisplayItem::PlanUpdate(_, payload) =
            DisplayItem::parse_value(&value).expect("typed plan")
        else {
            panic!("expected plan update");
        };
        assert_eq!(payload.plan[0].status, PlanStepStatus::InProgress);
        assert_eq!(payload.plan[1].status, PlanStepStatus::Pending);
    }

    #[test]
    fn heartbeat_fields_are_not_inferred_from_unstructured_text() {
        let base = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "heartbeat",
            "item_id": "heartbeat-1",
            "call_id": "heartbeat-1",
            "tool_name": "llm_request",
            "status": "running",
            "action": "request",
            "visibility": "user",
            "payload": {
                "title": "LLM request",
                "request": "stage artifact",
                "elapsed_ms": 5250,
                "transcript": "LLM request: stage artifact  Elapsed 5.2s"
            }
        });
        let DisplayItem::Heartbeat(_, payload) =
            DisplayItem::parse_value(&base).expect("structured heartbeat")
        else {
            panic!("expected heartbeat");
        };
        assert_eq!(payload.request, "stage artifact");
        assert_eq!(payload.elapsed_ms, 5250);

        let mut legacy = base;
        legacy["payload"]
            .as_object_mut()
            .expect("payload")
            .remove("request");
        legacy["payload"]
            .as_object_mut()
            .expect("payload")
            .remove("elapsed_ms");
        legacy["payload"]["text"] = Value::String("stage artifact  Elapsed 5.2s".to_string());
        let DisplayItem::Heartbeat(_, legacy_payload) =
            DisplayItem::parse_value(&legacy).expect("legacy heartbeat")
        else {
            panic!("expected legacy heartbeat");
        };
        assert!(legacy_payload.request.is_empty());
        assert_eq!(legacy_payload.elapsed_ms, 0);
    }

    #[test]
    fn model_activity_payload_preserves_semantic_action_and_elapsed_time() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "model_activity",
            "item_id": "turn-1:model:direct_qa:a1:c0:t3",
            "call_id": "turn-1:model:direct_qa:a1:c0:t3",
            "tool_name": "model",
            "status": "completed",
            "action": "compose_response",
            "visibility": "user",
            "payload": {
                "label": "Integrated tool failure and composed response",
                "elapsed_ms": 46200,
                "stage": "direct_qa",
                "attempt": 1,
                "continuation_index": 0,
                "turn_index": 3,
                "outcome": "composed_result",
                "transcript": "Agent · Integrated tool failure and composed response · 46.2s"
            }
        });

        let DisplayItem::ModelActivity(common, payload) =
            DisplayItem::parse_value(&value).expect("model activity")
        else {
            panic!("expected model activity");
        };
        assert_eq!(common.action, "compose_response");
        assert_eq!(
            payload.label,
            "Integrated tool failure and composed response"
        );
        assert_eq!(payload.elapsed_ms, 46_200);
        assert_eq!(payload.turn_index, 3);
    }

    #[test]
    fn review_cycle_payload_is_structured() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "review_cycle",
            "item_id": "review-cycle:run-1:output-1:a1",
            "call_id": "review-cycle:run-1:output-1:a1",
            "tool_name": "stage_review",
            "status": "running",
            "action": "review_cycle",
            "visibility": "user",
            "payload": {
                "title": "Review cycle",
                "stage": "output_synthesis",
                "stage_instance_id": "output-1",
                "attempt": 1,
                "review_kind": "readiness",
                "round": 1,
                "max_rounds": 2,
                "phase": "reviewing",
                "outcome": "pending",
                "summary": "",
                "feedback_format": "structured_v2",
                "findings": [{
                    "severity": "P2",
                    "title": "Clarify evidence scope",
                    "description": "The evidence boundary is unclear.",
                    "evidence_refs": ["report.md:17"],
                    "display_evidence_refs": ["report.md:17"],
                    "remediation": "State the supported scope."
                }],
                "overall_assessment": "One ordinary issue remains.",
                "review_record_id": "feedback-output-readiness-1",
                "gate_disposition": "decision_basis",
                "gate_decision": "accept",
                "elapsed_ms": 250
            }
        });
        let DisplayItem::ReviewCycle(common, payload) =
            DisplayItem::parse_value(&value).expect("review cycle item")
        else {
            panic!("expected review cycle");
        };
        assert_eq!(common.item_id, "review-cycle:run-1:output-1:a1");
        assert_eq!(payload.review_kind, "readiness");
        assert_eq!(payload.round, 1);
        assert_eq!(payload.max_rounds, 2);
        assert_eq!(payload.elapsed_ms, 250);
        assert_eq!(payload.feedback_format, "structured_v2");
        assert_eq!(payload.findings.len(), 1);
        assert_eq!(payload.findings[0].severity, "P2");
        assert_eq!(payload.findings[0].evidence_refs, ["report.md:17"]);
        assert_eq!(payload.findings[0].display_evidence_refs, ["report.md:17"]);
        assert_eq!(payload.overall_assessment, "One ordinary issue remains.");
        assert_eq!(payload.review_record_id, "feedback-output-readiness-1");
        assert_eq!(payload.gate_disposition, "decision_basis");
        assert_eq!(payload.gate_decision, "accept");
    }

    #[test]
    fn stage_payload_preserves_structured_gate_provenance() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "stage",
            "item_id": "stage:literature_grounding:a1:outcome",
            "call_id": "stage:literature_grounding:a1:outcome",
            "tool_name": "stage_lifecycle",
            "status": "completed",
            "action": "completed",
            "visibility": "user",
            "payload": {
                "stage": "literature_grounding",
                "stage_label": "Literature grounding",
                "attempt": 1,
                "event": "completed",
                "gate_decision": "advance_with_residual_findings",
                "next_stage": "research_framing",
                "reason_code": "review_round_limit_reached",
                "reason_display": "Review limit reached; 1 finding carried forward.",
                "basis_review_record_id": "feedback-reviewer-2",
                "basis_review_kind": "reviewer",
                "basis_review_round": 2,
                "active_finding_count": 1,
                "residual_review_record_id": "feedback-reviewer-2",
                "residual_finding_count": 1,
                "carry_in_review_record_ids": ["feedback-reviewer-1"]
            }
        });

        let DisplayItem::Stage(_, payload) = DisplayItem::parse_value(&value).expect("stage item")
        else {
            panic!("expected stage item");
        };
        assert_eq!(payload.reason_code, "review_round_limit_reached");
        assert_eq!(payload.basis_review_record_id, "feedback-reviewer-2");
        assert_eq!(payload.basis_review_round, 2);
        assert_eq!(payload.active_finding_count, 1);
        assert_eq!(payload.residual_finding_count, 1);
        assert_eq!(payload.carry_in_review_record_ids, ["feedback-reviewer-1"]);
    }

    #[test]
    fn action_required_payload_is_nonterminal_protocol_state() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "action_required",
            "item_id": "turn-1:action-required",
            "call_id": "turn-1:action-required",
            "tool_name": "interaction_protocol",
            "status": "paused_for_human",
            "action": "resolve_protocol_error",
            "visibility": "user",
            "payload": {
                "title": "Action required",
                "reason_code": "paused_for_human_without_pending_session",
                "detail": "Runtime has no actionable pending interaction.",
                "run_dir": "/tmp/run-1",
                "recoverable": false
            }
        });

        let DisplayItem::ActionRequired(common, payload) =
            DisplayItem::parse_value(&value).expect("action required")
        else {
            panic!("expected action required");
        };
        assert_eq!(common.status, "paused_for_human");
        assert_eq!(
            payload.reason_code,
            "paused_for_human_without_pending_session"
        );
        assert!(!payload.recoverable);
    }

    #[test]
    fn permission_activity_parses_safe_subject_and_group_identity() {
        let value = serde_json::json!({
            "schema_version": DISPLAY_ITEM_SCHEMA,
            "kind": "permission_activity",
            "item_id": "permission:one",
            "call_id": "call:one",
            "tool_name": "citation_resolve",
            "status": "completed",
            "action": "resolve",
            "visibility": "user",
            "payload": {
                "interaction_id": "one",
                "interaction_kind": "permission_request",
                "lifecycle": "approved",
                "decision_scope": "once",
                "subject": {
                    "kind": "paper",
                    "primary": "Machine translation of cortical activity to text",
                    "secondary": "Makin (2020)",
                    "qualifiers": ["10.1038/s41593-020-0608-8"]
                },
                "display_group_id": "permission-sequence:literature"
            }
        });

        let DisplayItem::PermissionActivity(_, payload) =
            DisplayItem::parse_value(&value).expect("permission activity")
        else {
            panic!("expected permission activity");
        };
        assert_eq!(payload.subject.kind, "paper");
        assert_eq!(
            payload.subject.primary,
            "Machine translation of cortical activity to text"
        );
        assert_eq!(payload.subject.secondary, "Makin (2020)");
        assert_eq!(payload.subject.qualifiers, ["10.1038/s41593-020-0608-8"]);
        assert_eq!(payload.display_group_id, "permission-sequence:literature");
    }
}
