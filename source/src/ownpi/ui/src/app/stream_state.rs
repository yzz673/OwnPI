//! Display-only ownership of streamed public text and its canonical handoff.
//! Never promotes a preview, a source-list, or a withdrawn attempt to a result.
use super::{text, Value};
use serde_json::json;
use std::collections::{BTreeMap, VecDeque};

const MAX_MESSAGES: usize = 128;
const MAX_TEXT_BYTES: usize = 1024 * 1024;
const MAX_TOTAL_BYTES: usize = 4 * MAX_TEXT_BYTES;
const MAX_RECEIPTS: usize = 4096;
const MAX_DIAGNOSTICS: usize = 128;

#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord)]
struct Identity {
    run: String,
    actor: String,
    stage_attempt: u64,
    item: String,
    conversation: String,
    response: String,
    provider_attempt: Option<u64>,
}
impl Identity {
    fn parse(value: &Value) -> Option<Self> {
        let scope = value.get("stream_key")?.as_array()?;
        if scope.len() != 4 {
            return None;
        }
        Some(Self {
            run: scope[0].as_str()?.into(),
            actor: scope[1].as_str()?.into(),
            stage_attempt: scope[2].as_u64()?,
            item: scope[3].as_str()?.into(),
            conversation: value["agent_ref"]["conversation_id"]
                .as_str()
                .or(value["conversation_id"].as_str())
                .unwrap_or_default()
                .into(),
            response: text(value, "response_id"),
            provider_attempt: value["provider_attempt"].as_u64(),
        })
    }
    fn matches(&self, other: &Self, item: bool) -> bool {
        self.run == other.run
            && self.actor == other.actor
            && self.stage_attempt == other.stage_attempt
            && (!item || self.item == other.item)
            && (self.conversation.is_empty()
                || other.conversation.is_empty()
                || self.conversation == other.conversation)
            && (self.response.is_empty()
                || other.response.is_empty()
                || self.response == other.response)
            && (self.provider_attempt.is_none()
                || other.provider_attempt.is_none()
                || self.provider_attempt == other.provider_attempt)
    }
    fn diagnostic(&self) -> Value {
        json!({"run":self.run,"actor":self.actor,"stage_attempt":self.stage_attempt,
            "conversation":self.conversation,"response":self.response,"item":self.item,
            "provider_attempt":self.provider_attempt})
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Streaming,
    AwaitingRecord,
    Mismatch,
    Unconfirmed,
    Withdrawn,
}

pub(super) struct LiveMessage {
    pub content: String,
    pub context: Value,
    pub order: u64,
    phase: Phase,
}
impl LiveMessage {
    pub fn display_text(&self, details: bool) -> String {
        match self.phase {
            Phase::Streaming | Phase::AwaitingRecord => self.content.clone(),
            Phase::Mismatch => format!(
                "Output record mismatch · Unconfirmed preview\n\n{}",
                self.content
            ),
            Phase::Unconfirmed => format!(
                "Unconfirmed output · No matching final record\n\n{}",
                self.content
            ),
            Phase::Withdrawn if details => {
                format!("Output withdrawn · Not a final result\n\n{}", self.content)
            }
            Phase::Withdrawn => {
                "Output withdrawn · Retry or stream error (details in transcript)".into()
            }
        }
    }
}

#[derive(Default)]
pub(super) struct StreamState {
    entries: BTreeMap<Identity, LiveMessage>,
    committed: VecDeque<Receipt>,
    diagnostics: VecDeque<Value>,
    next_order: u64,
    pub evicted: usize,
}

struct Receipt {
    record_id: String,
    identity: Identity,
}

impl StreamState {
    pub fn values(&self) -> impl Iterator<Item = &LiveMessage> {
        self.entries.values()
    }
    #[cfg(test)]
    pub fn len(&self) -> usize {
        self.entries.len()
    }
    #[cfg(test)]
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }
    pub fn clear(&mut self) {
        *self = Self::default();
    }
    fn log(&mut self, identity: &Identity, outcome: &str, bytes: usize) {
        if self.diagnostics.len() == MAX_DIAGNOSTICS {
            self.diagnostics.pop_front();
        }
        self.diagnostics.push_back(
            json!({"identity":identity.diagnostic(),"outcome":outcome,"public_text_bytes":bytes}),
        );
    }
    pub fn audit(&self) -> String {
        format!(
            "\n\n# Frontend text lifecycle\n\nDisplay capacity evictions: {}\n\n```json\n{}\n```\n",
            self.evicted,
            serde_json::to_string_pretty(&self.diagnostics).unwrap_or_default()
        )
    }
    fn remember(&mut self, identity: Identity, record_id: String) {
        if !self
            .committed
            .iter()
            .any(|receipt| receipt.identity == identity && receipt.record_id == record_id)
        {
            if self.committed.len() == MAX_RECEIPTS {
                self.committed.pop_front();
                self.evicted += 1;
            }
            self.committed.push_back(Receipt {
                record_id,
                identity,
            });
        }
    }
    pub fn observe(&mut self, data: &Value) {
        let Some(identity) = Identity::parse(data) else {
            return;
        };
        let kind = text(data, "canonical_event_type");
        if matches!(
            text(data, "message_purpose").as_str(),
            "answer_candidate" | "unclassified"
        ) {
            self.entries.retain(|key, _| !key.matches(&identity, true));
            self.log(&identity, "candidate_withheld_until_publication", 0);
            return;
        }
        if kind == "assistant_text_delta" && !identity.item.is_empty() {
            if self
                .committed
                .iter()
                .any(|old| old.identity.matches(&identity, true))
            {
                self.log(
                    &identity,
                    "late_delta_after_commit",
                    text(data, "delta").len(),
                );
                return;
            }
            let delta = text(data, "delta");
            // Empty public text can precede a real delta; do not render it as narration.
            if !self.entries.contains_key(&identity) {
                self.next_order += 1;
                self.entries.insert(
                    identity.clone(),
                    LiveMessage {
                        content: String::new(),
                        context: json!({}),
                        order: self.next_order,
                        phase: Phase::Streaming,
                    },
                );
            }
            let entry = self.entries.get_mut(&identity).unwrap();
            if matches!(
                entry.phase,
                Phase::Withdrawn | Phase::AwaitingRecord | Phase::Mismatch
            ) {
                return;
            }
            for key in ["agent_ref", "scope", "audit_context"] {
                if data[key].is_object() {
                    entry.context[key] = data[key].clone();
                }
            }
            if entry.context["scope"].is_null() {
                entry.context["scope"] = json!({"actor_id":identity.actor,"stage":data["stage"],"attempt":identity.stage_attempt});
            }
            entry.content.push_str(&delta);
            self.enforce_capacity();
        } else if matches!(
            kind.as_str(),
            "response_completed" | "attempt_discarded" | "stream_error"
        ) {
            // A stage/actor is not a response. Even a single current preview
            // could follow the older request whose unscoped error arrived late.
            if identity.response.is_empty() && identity.item.is_empty() {
                self.log(&identity, "unscoped_terminal_not_applied", 0);
                return;
            }
            let affected = self
                .entries
                .keys()
                .filter(|key| key.matches(&identity, !identity.item.is_empty()))
                .cloned()
                .collect::<Vec<_>>();
            // Missing retry/conversation identity cannot choose between requests.
            let requests = affected
                .iter()
                .map(|key| (&key.conversation, &key.response, key.provider_attempt))
                .collect::<std::collections::BTreeSet<_>>();
            if requests.len() > 1 {
                self.log(&identity, "ambiguous_terminal_not_applied", 0);
                return;
            }
            for key in affected {
                let entry = self.entries.get_mut(&key).unwrap();
                if entry.phase == Phase::Withdrawn {
                    continue;
                }
                if kind == "response_completed" {
                    if entry.phase == Phase::Streaming {
                        entry.phase = Phase::AwaitingRecord;
                    }
                } else {
                    entry.phase = Phase::Withdrawn;
                }
                let bytes = entry.content.len();
                self.log(&key, &kind, bytes);
            }
        }
    }
    pub fn reconcile(&mut self, records: &[Value]) {
        for record in records {
            if record["kind"] != "agent_message"
                || record["payload"]["source_list"] == true
                || (record["payload"]["detail_only"] == true
                    && record["payload"]["review_withheld"] != true)
                || record["status"] != "completed"
            {
                continue;
            }
            let Some(mut identity) = Identity::parse(record) else {
                continue;
            };
            if identity.item.is_empty() {
                continue;
            }
            // Pin a canonical item to the response that it actually committed.
            // Later snapshots may omit response/retry fields; they must not
            // broaden the old receipt and consume a newer response.
            let record_id = text(record, "item_id");
            if !record_id.is_empty() {
                if let Some(receipt) = self.committed.iter().find(|receipt| {
                    receipt.record_id == record_id && receipt.identity.matches(&identity, true)
                }) {
                    identity = receipt.identity.clone();
                }
            }
            let body = text(&record["payload"], "text");
            let matches = self
                .entries
                .keys()
                .filter(|key| key.matches(&identity, true))
                .cloned()
                .collect::<Vec<_>>();
            if matches.len() > 1 {
                self.log(&identity, "ambiguous_canonical_identity", body.len());
                continue;
            }
            if body.trim().is_empty() {
                if let Some(key) = matches.first() {
                    let entry = self.entries.get_mut(key).unwrap();
                    if entry.phase != Phase::Withdrawn && !entry.content.trim().is_empty() {
                        if entry.phase != Phase::Mismatch {
                            entry.phase = Phase::Mismatch;
                            self.log(key, "blank_canonical_body", body.len());
                        }
                    }
                }
            } else {
                if let Some(key) = matches.first() {
                    let entry = self.entries.remove(key).unwrap();
                    self.log(
                        key,
                        if entry.content == body {
                            "committed"
                        } else {
                            "canonical_revised_text"
                        },
                        body.len(),
                    );
                    identity = key.clone();
                }
                self.remember(identity, record_id);
            }
        }
    }
    pub fn finish(&mut self) {
        for entry in self.entries.values_mut() {
            if matches!(entry.phase, Phase::Streaming | Phase::AwaitingRecord) {
                entry.phase = Phase::Unconfirmed;
            }
        }
    }
    fn enforce_capacity(&mut self) {
        loop {
            let oversized = self
                .entries
                .iter()
                .find(|(_, entry)| entry.content.len() > MAX_TEXT_BYTES)
                .map(|(key, _)| key.clone());
            let over = self.entries.len() > MAX_MESSAGES
                || self
                    .entries
                    .values()
                    .map(|entry| entry.content.len())
                    .sum::<usize>()
                    > MAX_TOTAL_BYTES;
            let remove = oversized.or_else(|| {
                over.then(|| {
                    self.entries
                        .iter()
                        .min_by_key(|(_, entry)| entry.order)
                        .unwrap()
                        .0
                        .clone()
                })
            });
            let Some(key) = remove else {
                break;
            };
            let entry = self.entries.remove(&key).unwrap();
            self.evicted += 1;
            self.log(&key, "display_capacity_exceeded", entry.content.len());
            self.remember(key, String::new());
        }
    }
}
