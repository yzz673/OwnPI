//! JSON parsing helpers for human-interaction bridge payloads.
//!
//! Re-exported from `bottom_pane` so existing `crate::bottom_pane::parse_*`
//! call sites keep working.
use super::*;

pub(crate) fn parse_question(value: &Value) -> Option<ClarificationQuestion> {
    let object = value.as_object()?;
    Some(ClarificationQuestion {
        question_id: object
            .get("question_id")
            .or_else(|| object.get("id"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        header: object
            .get("header")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        question: object
            .get("question")
            .or_else(|| object.get("label"))
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_string(),
        required: object
            .get("required")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        multi_select: object
            .get("multi_select")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        is_other: object
            .get("is_other")
            .and_then(Value::as_bool)
            .unwrap_or(true),
        is_secret: object
            .get("is_secret")
            .and_then(Value::as_bool)
            .unwrap_or(false),
        options: object
            .get("options")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .map(|item| PendingOption {
                        id: item
                            .get("label")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        label: item
                            .get("label")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                        description: item
                            .get("description")
                            .and_then(Value::as_str)
                            .unwrap_or_default()
                            .to_string(),
                    })
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default(),
    })
}
