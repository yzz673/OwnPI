#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub(crate) struct AgentMessageContext {
    pub(crate) stage: String,
    pub(crate) attempt: i64,
    pub(crate) component: String,
    pub(crate) operation: String,
    pub(crate) round_id: String,
}
