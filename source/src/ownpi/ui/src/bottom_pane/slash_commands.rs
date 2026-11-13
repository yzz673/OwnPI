#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) struct ServiceTierCommand {
    pub(crate) id: String,
    pub(crate) name: String,
    pub(crate) description: String,
    pub(crate) source: String,
    pub(crate) kind: String,
    pub(crate) owner: String,
    pub(crate) panel_id: String,
    pub(crate) aliases: Vec<String>,
    pub(crate) argument_hint: String,
    pub(crate) disabled: bool,
    pub(crate) disabled_reason: String,
    pub(crate) shortcut: String,
    pub(crate) tab: String,
    pub(crate) side_content: String,
}

impl ServiceTierCommand {
    #[cfg(test)]
    pub(crate) fn catalog_entry(
        id: impl Into<String>,
        name: impl Into<String>,
        description: impl Into<String>,
        source: impl Into<String>,
        kind: impl Into<String>,
    ) -> Self {
        Self {
            id: id.into(),
            name: name.into(),
            description: description.into(),
            source: source.into(),
            kind: kind.into(),
            owner: String::new(),
            panel_id: String::new(),
            aliases: Vec::new(),
            argument_hint: String::new(),
            disabled: false,
            disabled_reason: String::new(),
            shortcut: String::new(),
            tab: String::new(),
            side_content: String::new(),
        }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub(crate) enum SlashCommandItem {
    ServiceTier(ServiceTierCommand),
}

impl SlashCommandItem {
    pub(crate) fn command(&self) -> &str {
        match self {
            Self::ServiceTier(command) => command.name.trim_start_matches('/'),
        }
    }

    pub(crate) fn source(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.source,
        }
    }

    pub(crate) fn kind(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.kind,
        }
    }

    pub(crate) fn description(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.description,
        }
    }

    pub(crate) fn display_name(&self) -> String {
        format!("/{}", self.command())
    }

    pub(crate) fn aliases(&self) -> &[String] {
        match self {
            Self::ServiceTier(command) => &command.aliases,
        }
    }

    pub(crate) fn is_disabled(&self) -> bool {
        match self {
            Self::ServiceTier(command) => command.disabled,
        }
    }

    pub(crate) fn disabled_reason(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.disabled_reason,
        }
    }

    pub(crate) fn shortcut(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.shortcut,
        }
    }

    pub(crate) fn tab(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.tab,
        }
    }

    pub(crate) fn side_content(&self) -> &str {
        match self {
            Self::ServiceTier(command) => &command.side_content,
        }
    }
}

pub(crate) fn commands_for_input(
    service_tier_commands: &[ServiceTierCommand],
) -> Vec<SlashCommandItem> {
    service_tier_commands
        .iter()
        .cloned()
        .map(SlashCommandItem::ServiceTier)
        .collect()
}
