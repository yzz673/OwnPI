from __future__ import annotations

import os
from dataclasses import asdict, dataclass, field
from typing import Any

from ownpi.runtime.limits.recovery import (
    DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_PROVIDER_SSE_IDLE_TIMEOUT_SECONDS,
    DEFAULT_REQUEST_MAX_RETRIES,
    DEFAULT_STREAM_MAX_RETRIES,
)
from ownpi.runtime.limits.projection import (
    DEFAULT_CONVERSATION_LOOKUP_CACHE_ITEMS,
    DEFAULT_CONVERSATION_PAGE_ITEMS,
)
from ownpi.runtime.limits.workflow import review_profile_selection_from_value
from ownpi.runtime.limits.resources import (
    PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS,
)
from ownpi.schemas.workflow_plan import runtime_profile_selection_from_value
from ownpi.schemas.runtime_permissions_config import RuntimePermissionsConfig


VALID_WEB_SEARCH_MODES = {"disabled", "cached", "indexed", "live"}
VALID_WEB_SEARCH_CONTEXT_SIZES = {"low", "medium", "high"}
VALID_WEB_SEARCH_CONTENT_TYPES = {"text", "image"}


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key_env: str | None = None
    base_url: str | None = None
    base_url_env: str | None = None
    api_style: str = "openai_chat"
    endpoint_path: str | None = None
    model_options: list[str] = field(default_factory=list)
    reasoning_effort: str | None = None
    reasoning_options: list[str] = field(default_factory=list)
    request_timeout_seconds: int = DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS
    sse_idle_timeout_seconds: int = DEFAULT_PROVIDER_SSE_IDLE_TIMEOUT_SECONDS
    stream_responses: bool = True
    request_max_retries: int | None = None
    stream_max_retries: int | None = None
    retry_backoff_seconds: float = 0.2
    context_window_limit: int | None = None
    output_reserve_tokens: int | None = None
    max_output_tokens: int | None = None
    context_safety_margin_tokens: int | None = None
    input_modalities: tuple[str, ...] = ("text", "image")
    native_tool_search_output: bool | None = None
    native_tool_strict: bool = False
    supports_parallel_tool_calls: bool | None = None
    supports_hosted_web_search: bool = True
    supports_standalone_web_search: bool = False
    web_search_mode: str = "live"
    web_search_context_size: str | None = None
    web_search_allowed_domains: tuple[str, ...] = ()
    web_search_user_location: tuple[tuple[str, str], ...] = ()
    web_search_content_types: tuple[str, ...] = ()
    web_search_max_uses: int | None = None

    def __post_init__(self) -> None:
        if self.web_search_mode not in VALID_WEB_SEARCH_MODES:
            raise ValueError(
                f"web_search_mode must be one of {sorted(VALID_WEB_SEARCH_MODES)}"
            )
        if (
            self.web_search_context_size is not None
            and self.web_search_context_size not in VALID_WEB_SEARCH_CONTEXT_SIZES
        ):
            raise ValueError(
                "web_search_context_size must be low, medium, high, or omitted"
            )
        invalid_content_types = (
            set(self.web_search_content_types) - VALID_WEB_SEARCH_CONTENT_TYPES
        )
        if invalid_content_types:
            raise ValueError(
                f"unsupported web_search_content_types: {sorted(invalid_content_types)}"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LLMConfig":
        if "max_retries" in data:
            raise ValueError(
                "max_retries is not supported; use request_max_retries, "
                "and stream_max_retries"
            )
        removed_operation_retry_fields = (
            "runtime_resume_max_retries",
            "runtime_resume_retry_backoff_seconds",
        )
        configured_removed_fields = [
            key for key in removed_operation_retry_fields if key in data
        ]
        if configured_removed_fields:
            raise ValueError(
                "operation retry fields are not supported: "
                + ", ".join(configured_removed_fields)
            )
        return cls(
            provider=str(data["provider"]),
            model=str(data["model"]),
            api_key_env=data.get("api_key_env"),
            base_url=data.get("base_url"),
            base_url_env=data.get("base_url_env"),
            api_style=str(data.get("api_style", "openai_chat")),
            endpoint_path=data.get("endpoint_path"),
            model_options=[str(item) for item in data.get("model_options", [])],
            reasoning_effort=data.get("reasoning_effort"),
            reasoning_options=[str(item) for item in data.get("reasoning_options", [])],
            request_timeout_seconds=max(
                1,
                int(
                    data.get(
                        "request_timeout_seconds",
                        DEFAULT_PROVIDER_REQUEST_TIMEOUT_SECONDS,
                    )
                ),
            ),
            sse_idle_timeout_seconds=max(
                1,
                int(
                    data.get(
                        "sse_idle_timeout_seconds",
                        DEFAULT_PROVIDER_SSE_IDLE_TIMEOUT_SECONDS,
                    )
                ),
            ),
            stream_responses=bool(data.get("stream_responses", True)),
            request_max_retries=_optional_nonnegative_int(
                data.get("request_max_retries"),
                field_name="request_max_retries",
            ),
            stream_max_retries=_optional_nonnegative_int(
                data.get("stream_max_retries"),
                field_name="stream_max_retries",
            ),
            retry_backoff_seconds=max(
                0.0, float(data.get("retry_backoff_seconds", 0.2))
            ),
            context_window_limit=_optional_positive_int(
                data.get("context_window_limit")
            ),
            output_reserve_tokens=_optional_positive_int(
                data.get("output_reserve_tokens", data.get("output_reserve"))
            ),
            max_output_tokens=_optional_positive_int(data.get("max_output_tokens")),
            context_safety_margin_tokens=_optional_positive_int(
                data.get(
                    "context_safety_margin_tokens", data.get("safety_margin_tokens")
                )
            ),
            input_modalities=_coerce_modality_tuple(
                data.get("input_modalities", ("text", "image"))
            ),
            native_tool_search_output=(
                bool(data["native_tool_search_output"])
                if data.get("native_tool_search_output") is not None
                else None
            ),
            native_tool_strict=bool(data.get('native_tool_strict', False)),
            supports_parallel_tool_calls=(
                bool(data["supports_parallel_tool_calls"])
                if data.get("supports_parallel_tool_calls") is not None
                else None
            ),
            supports_hosted_web_search=bool(data.get("supports_hosted_web_search", True)),
            supports_standalone_web_search=bool(
                data.get("supports_standalone_web_search", False)
            ),
            web_search_mode=str(data.get("web_search_mode", "live") or "live")
            .strip()
            .lower(),
            web_search_context_size=_optional_lower_string(
                data.get("web_search_context_size", data.get("search_context_size"))
            ),
            web_search_allowed_domains=tuple(
                str(item).strip()
                for item in _coerce_string_list(
                    data.get(
                        "web_search_allowed_domains", data.get("allowed_domains", [])
                    )
                )
                if str(item).strip()
            ),
            web_search_user_location=_coerce_web_search_user_location(
                data.get("web_search_user_location", data.get("user_location"))
            ),
            web_search_content_types=tuple(
                str(item).strip().lower()
                for item in _coerce_string_list(
                    data.get(
                        "web_search_content_types", data.get("search_content_types", [])
                    )
                )
                if str(item).strip()
            ),
            web_search_max_uses=_optional_positive_int(
                data.get("web_search_max_uses")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for key in ('request_max_retries', 'stream_max_retries'):
            if payload.get(key) is None:
                payload.pop(key, None)
        return payload

    @property
    def effective_request_max_retries(self) -> int:
        value = self.request_max_retries
        return DEFAULT_REQUEST_MAX_RETRIES if value is None else value

    @property
    def effective_stream_max_retries(self) -> int:
        value = self.stream_max_retries
        return DEFAULT_STREAM_MAX_RETRIES if value is None else value

    @property
    def retry_config_source(self) -> str:
        sources = (
            self._retry_budget_source('request'),
            self._retry_budget_source('stream'),
        )
        return '+'.join(dict.fromkeys(sources))

    def _retry_budget_source(self, kind: str) -> str:
        explicit = (
            self.stream_max_retries
            if kind == 'stream'
            else self.request_max_retries
        )
        if explicit is not None:
            return f'{kind}_max_retries'
        return 'codex_defaults'


@dataclass(frozen=True)
class WebToolsConfig:
    fetch_timeout_seconds: int = 20
    search_backend: str = "duckduckgo_html"
    user_agent: str = "ownpi/0.1 local-tool-runtime"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "WebToolsConfig":
        payload = data or {}
        return cls(
            fetch_timeout_seconds=max(1, int(payload.get("fetch_timeout_seconds", 20))),
            search_backend=str(
                payload.get("search_backend", "duckduckgo_html") or "duckduckgo_html"
            ),
            user_agent=str(
                payload.get("user_agent", "ownpi/0.1 local-tool-runtime")
                or "ownpi/0.1 local-tool-runtime"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MCPServerConfig:
    name: str
    transport: str = "stdio"
    command: list[str] = field(default_factory=list)
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    headers_helper: str | None = None
    oauth: dict[str, Any] = field(default_factory=dict)
    auth: dict[str, Any] = field(default_factory=dict)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    timeout_seconds: int = 20

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MCPServerConfig":
        return cls(
            name=str(data.get("name", "")).strip(),
            transport=str(data.get("transport", data.get("type", "stdio")) or "stdio")
            .strip()
            .lower(),
            command=_coerce_string_list(data.get("command", [])),
            url=(str(data["url"]) if data.get("url") is not None else None),
            headers=_coerce_string_mapping(data.get("headers", {})),
            headers_helper=(
                str(data.get("headers_helper", data.get("headersHelper")))
                if data.get("headers_helper", data.get("headersHelper")) is not None
                else None
            ),
            oauth=dict(data.get("oauth", {}))
            if isinstance(data.get("oauth"), dict)
            else {},
            auth=dict(data.get("auth", {}))
            if isinstance(data.get("auth"), dict)
            else {},
            env=_coerce_string_mapping(data.get("env", {})),
            cwd=(str(data["cwd"]) if data.get("cwd") is not None else None),
            timeout_seconds=max(1, int(data.get("timeout_seconds", 20))),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NotebookToolsConfig:
    max_cell_chars: int = 6000

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "NotebookToolsConfig":
        payload = data or {}
        return cls(max_cell_chars=max(100, int(payload.get("max_cell_chars", 6000))))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ImageToolsConfig:
    max_preview_pixels: int = 1_000_000

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ImageToolsConfig":
        payload = data or {}
        return cls(
            max_preview_pixels=max(1, int(payload.get("max_preview_pixels", 1_000_000)))
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _bounded_int(
    value: Any,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    parsed = default if value is None else int(value)
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"value must be between {minimum} and {maximum}")
    return parsed


@dataclass(frozen=True)
class ExternalKnowledgeConfig:
    enabled: bool = False
    delivery_root: str = ""
    max_tool_result_chars: int = 6000

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExternalKnowledgeConfig":
        payload = data or {}
        allowed_fields = {
            "enabled",
            "delivery_root",
            "max_tool_result_chars",
        }
        unknown_fields = sorted(set(payload).difference(allowed_fields))
        if unknown_fields:
            raise ValueError(
                "runtime.external_knowledge contains unsupported fields: "
                f"{', '.join(unknown_fields)}"
            )
        return cls(
            enabled=bool(payload.get("enabled", False)),
            delivery_root=str(payload.get("delivery_root", "") or "").strip(),
            max_tool_result_chars=_bounded_int(
                payload.get("max_tool_result_chars", 6000),
                default=6000,
                minimum=1000,
                maximum=24000,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class InternalMemoryConfig:
    enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "InternalMemoryConfig":
        payload = data or {}
        return cls(enabled=bool(payload.get("enabled", False)))

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class HookHandlerConfig:
    handler_type: str = "command"
    command: tuple[str, ...] = ()
    mcp_server: str = ""
    mcp_tool: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "sync"
    name: str = ""
    timeout_seconds: int = 10
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    inherit_env: bool = False
    matcher: str = ""
    source: str = ""

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, source: str = ""
    ) -> "HookHandlerConfig":
        handler_type = str(data.get("type", "command") or "command").strip().lower()
        if handler_type not in {"command", "mcp_tool"}:
            raise ValueError(f"unsupported Hook handler type: {handler_type}")
        execution_mode = str(data.get("mode", "sync") or "sync").strip().lower()
        if execution_mode not in {"sync", "async"}:
            raise ValueError(f"unsupported Hook execution mode: {execution_mode}")
        command = tuple(_coerce_string_list(data.get("command", [])))
        mcp_server = str(data.get("mcp_server", "") or "").strip()
        mcp_tool = str(data.get("mcp_tool", "") or "").strip()
        if handler_type == "command" and not command:
            raise ValueError("command Hook handler requires command")
        if handler_type == "mcp_tool" and (not mcp_server or not mcp_tool):
            raise ValueError("mcp_tool Hook handler requires mcp_server and mcp_tool")
        if execution_mode == "async" and handler_type == "mcp_tool":
            raise ValueError("mcp_tool Hook handlers only support sync execution")
        return cls(
            handler_type=handler_type,
            command=command,
            mcp_server=mcp_server,
            mcp_tool=mcp_tool,
            arguments=(
                dict(data.get("arguments", {}))
                if isinstance(data.get("arguments"), dict)
                else {}
            ),
            execution_mode=execution_mode,
            name=str(data.get("name", "") or ""),
            timeout_seconds=max(1, int(data.get("timeout_seconds", 10) or 10)),
            cwd=(str(data["cwd"]) if data.get("cwd") is not None else None),
            env=_coerce_string_mapping(data.get("env")),
            inherit_env=bool(data.get("inherit_env", False)),
            matcher=str(data.get("matcher", data.get("matches", "")) or ""),
            source=str(data.get("source", source) or ""),
        )

    def with_source(self, source: str) -> "HookHandlerConfig":
        return HookHandlerConfig(
            handler_type=self.handler_type,
            command=tuple(self.command),
            mcp_server=self.mcp_server,
            mcp_tool=self.mcp_tool,
            arguments=dict(self.arguments),
            execution_mode=self.execution_mode,
            name=self.name,
            timeout_seconds=self.timeout_seconds,
            cwd=self.cwd,
            env=dict(self.env),
            inherit_env=self.inherit_env,
            matcher=self.matcher,
            source=source,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.handler_type,
            "command": list(self.command),
            "mcp_server": self.mcp_server,
            "mcp_tool": self.mcp_tool,
            "arguments": dict(self.arguments),
            "mode": self.execution_mode,
            "timeout_seconds": self.timeout_seconds,
            "cwd": self.cwd,
            "env": dict(self.env),
            "inherit_env": self.inherit_env,
            "matcher": self.matcher,
            "source": self.source,
        }


@dataclass(frozen=True)
class HookRegistryConfig:
    enabled: bool = False
    events: dict[str, list[HookHandlerConfig]] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None, *, source: str = "settings"
    ) -> "HookRegistryConfig":
        payload = data or {}
        config_source = str(payload.get("source", source) or "")
        raw_events = (
            payload.get("events", {}) if isinstance(payload.get("events"), dict) else {}
        )
        events: dict[str, list[HookHandlerConfig]] = {}
        for event_name, raw_specs in raw_events.items():
            if isinstance(raw_specs, dict):
                raw_items = [raw_specs]
            elif isinstance(raw_specs, list):
                raw_items = raw_specs
            else:
                raw_items = []
            specs = [
                HookHandlerConfig.from_dict(item, source=config_source)
                for item in raw_items
                if isinstance(item, dict)
            ]
            if specs:
                events[str(event_name)] = specs
        merged = cls(enabled=bool(payload.get("enabled", bool(events))), events=events)
        raw_sources = payload.get("sources", {})
        source_configs: list[HookRegistryConfig] = []
        if isinstance(raw_sources, dict):
            for source_name, source_payload in raw_sources.items():
                if isinstance(source_payload, dict):
                    source_configs.append(
                        cls.from_dict(source_payload, source=str(source_name))
                    )
        elif isinstance(raw_sources, list):
            for index, source_payload in enumerate(raw_sources):
                if isinstance(source_payload, dict):
                    source_name = str(
                        source_payload.get("source", f"source:{index}")
                        or f"source:{index}"
                    )
                    source_configs.append(
                        cls.from_dict(source_payload, source=source_name)
                    )
        if source_configs:
            merged = cls.merge(merged, *source_configs)
        return merged

    @classmethod
    def from_events_dict(
        cls, events: dict[str, Any] | None, *, source: str
    ) -> "HookRegistryConfig":
        if not isinstance(events, dict):
            return cls()
        return cls.from_dict(
            {"enabled": True, "events": events, "source": source}, source=source
        )

    @classmethod
    def merge(cls, *configs: "HookRegistryConfig | None") -> "HookRegistryConfig":
        events: dict[str, list[HookHandlerConfig]] = {}
        enabled = False
        for config in configs:
            if config is None:
                continue
            enabled = enabled or bool(config.enabled)
            for event_name, specs in config.events.items():
                events.setdefault(str(event_name), []).extend(list(specs))
        return cls(enabled=bool(enabled and events), events=events)

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "events": {
                event_name: [item.to_dict() for item in specs]
                for event_name, specs in self.events.items()
            },
        }


@dataclass(frozen=True)
class BuiltinHooksConfig:
    enabled: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "BuiltinHooksConfig":
        payload = data or {}
        return cls(
            enabled=bool(payload.get("enabled", True)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
        }


@dataclass(frozen=True)
class SkillTrustConfig:
    trusted_sources: tuple[str, ...] = ()
    trusted_plugins: tuple[str, ...] = ()
    trusted_remote_sources: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "SkillTrustConfig":
        payload = data or {}
        return cls(
            trusted_sources=tuple(
                _coerce_string_list(payload.get("trusted_sources", []))
            ),
            trusted_plugins=tuple(
                _coerce_string_list(payload.get("trusted_plugins", []))
            ),
            trusted_remote_sources=tuple(
                _coerce_string_list(payload.get("trusted_remote_sources", []))
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trusted_sources": list(self.trusted_sources),
            "trusted_plugins": list(self.trusted_plugins),
            "trusted_remote_sources": list(self.trusted_remote_sources),
        }


@dataclass(frozen=True)
class ExtensionTrustConfig:
    trusted_sources: tuple[str, ...] = ()
    trusted_plugins: tuple[str, ...] = ()
    trusted_hook_hashes: dict[str, str] = field(default_factory=dict)
    allow_project_hooks: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExtensionTrustConfig":
        payload = data or {}
        return cls(
            trusted_sources=tuple(
                _coerce_string_list(payload.get("trusted_sources", []))
            ),
            trusted_plugins=tuple(
                _coerce_string_list(payload.get("trusted_plugins", []))
            ),
            trusted_hook_hashes=_coerce_string_mapping(
                payload.get("trusted_hook_hashes", {})
            ),
            allow_project_hooks=bool(payload.get("allow_project_hooks", False)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "trusted_sources": list(self.trusted_sources),
            "trusted_plugins": list(self.trusted_plugins),
            "trusted_hook_hashes": dict(self.trusted_hook_hashes),
            "allow_project_hooks": self.allow_project_hooks,
        }


@dataclass(frozen=True)
class RuntimeUIConfig:
    theme: str = "auto"
    use_terminal_background: bool = True
    context_meter_enabled: bool = True
    context_meter_mode: str = "auto"
    context_meter_width: int = 24
    context_meter_show_threshold: bool = True

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuntimeUIConfig":
        payload = data or {}
        return cls(
            theme=str(payload.get("theme", "auto") or "auto"),
            use_terminal_background=bool(payload.get("use_terminal_background", True)),
            context_meter_enabled=bool(payload.get("context_meter_enabled", True)),
            context_meter_mode=str(payload.get("context_meter_mode", "auto") or "auto"),
            context_meter_width=max(8, int(payload.get("context_meter_width", 24))),
            context_meter_show_threshold=bool(
                payload.get("context_meter_show_threshold", True)
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class DockerEnvironmentConfig:
    enabled: bool = False
    image: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "DockerEnvironmentConfig":
        payload = data or {}
        return cls(
            enabled=bool(payload.get("enabled", False)),
            image=str(payload.get("image", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SSHRemoteTargetConfig:
    name: str
    host: str
    user: str = ""
    port: int = 22
    remote_workspace_root: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SSHRemoteTargetConfig":
        return cls(
            name=str(data.get("name", "") or "").strip(),
            host=str(data.get("host", "") or "").strip(),
            user=str(data.get("user", "") or "").strip(),
            port=max(1, int(data.get("port", 22) or 22)),
            remote_workspace_root=str(
                data.get("remote_workspace_root", "") or ""
            ).strip(),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class EnvironmentRoutingConfig:
    exec_default_provider: str = "host_local"
    destructive_exec_provider: str = "docker_sandbox"

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EnvironmentRoutingConfig":
        payload = data or {}
        from ownpi.runtime.environment.contracts import (
            normalize_environment_provider_kind,
        )

        return cls(
            exec_default_provider=normalize_environment_provider_kind(
                payload.get("exec_default_provider", "host_local") or "host_local"
            ),
            destructive_exec_provider=normalize_environment_provider_kind(
                payload.get("destructive_exec_provider", "docker_sandbox")
                or "docker_sandbox"
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LSPServerConfig:
    name: str
    command: list[str] = field(default_factory=list)
    languages: list[str] = field(default_factory=list)
    file_extensions: list[str] = field(default_factory=list)
    initialization_options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LSPServerConfig":
        return cls(
            name=str(data.get("name", "") or "").strip(),
            command=[str(item) for item in data.get("command", []) if str(item).strip()]
            if isinstance(data.get("command"), list)
            else [],
            languages=_coerce_string_list(data.get("languages", [])),
            file_extensions=_coerce_string_list(data.get("file_extensions", [])),
            initialization_options=(
                dict(data.get("initialization_options", {}))
                if isinstance(data.get("initialization_options"), dict)
                else {}
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LSPRuntimeConfig:
    enabled: bool = False
    request_timeout_seconds: int = 5
    servers: list[LSPServerConfig] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LSPRuntimeConfig":
        payload = data or {}
        return cls(
            enabled=bool(payload.get("enabled", False)),
            request_timeout_seconds=max(
                1, int(payload.get("request_timeout_seconds", 5) or 5)
            ),
            servers=[
                LSPServerConfig.from_dict(item)
                for item in payload.get("servers", [])
                if isinstance(item, dict)
                and str(item.get("name", "") or "").strip()
                and isinstance(item.get("command"), list)
                and item.get("command")
            ],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "request_timeout_seconds": self.request_timeout_seconds,
            "servers": [item.to_dict() for item in self.servers],
        }


@dataclass(frozen=True)
class LocalEnvironmentActivationConfig:
    kind: str = ""
    conda_sh: str = ""
    env_name: str = ""

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> "LocalEnvironmentActivationConfig":
        payload = data or {}
        return cls(
            kind=str(payload.get("kind", "") or ""),
            conda_sh=str(payload.get("conda_sh", "") or ""),
            env_name=str(payload.get("env_name", "") or ""),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class LocalEnvironmentConfig:
    default_env_name: str = ""
    default_python: str = ""
    activation: LocalEnvironmentActivationConfig = field(
        default_factory=LocalEnvironmentActivationConfig
    )
    env_vars: dict[str, str] = field(default_factory=dict)
    path_prepend: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "LocalEnvironmentConfig":
        payload = data or {}
        return cls(
            default_env_name=str(payload.get("default_env_name", "") or ""),
            default_python=str(payload.get("default_python", "") or ""),
            activation=LocalEnvironmentActivationConfig.from_dict(
                payload.get("activation")
            ),
            env_vars=_coerce_string_mapping(payload.get("env_vars")),
            path_prepend=_coerce_string_list(payload.get("path_prepend")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_env_name": self.default_env_name,
            "default_python": self.default_python,
            "activation": self.activation.to_dict(),
            "env_vars": dict(self.env_vars),
            "path_prepend": list(self.path_prepend),
        }


@dataclass(frozen=True)
class ResourceMaterializationConfig:
    overall_deadline_seconds: int = 600
    attempt_timeout_seconds: int = 120
    max_attempts: int = 3
    lock_timeout_seconds: int = 30
    max_receipt_chars: int = 16_384
    max_model_snapshot_bytes: int = 50_000_000_000
    max_repository_archive_bytes: int = 2_000_000_000
    max_repository_expanded_bytes: int = 2_000_000_000
    model_cache_root: str = "~/.ownpi/resources/models"
    repository_cache_root: str = "~/.ownpi/resources/repositories"
    huggingface_route_status: str = "unavailable"
    github_api_route_status: str = "unavailable"
    github_archive_route_status: str = "unavailable"
    github_ssh_route_status: str = "unavailable"
    github_https_git_route_status: str = "unavailable"
    github_api_endpoint: str = "https://api.github.com"
    github_archive_endpoint: str = "https://codeload.github.com"
    github_ssh_host_alias: str = ""
    github_ssh_identity_path: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResourceMaterializationConfig":
        payload = data or {}
        defaults = cls()
        route_status = {"available", "degraded", "unavailable"}
        return cls(
            overall_deadline_seconds=max(
                1, int(payload.get("overall_deadline_seconds", 600))
            ),
            attempt_timeout_seconds=max(
                1, int(payload.get("attempt_timeout_seconds", 120))
            ),
            max_attempts=max(1, min(8, int(payload.get("max_attempts", 3)))),
            lock_timeout_seconds=max(1, int(payload.get("lock_timeout_seconds", 30))),
            max_receipt_chars=max(
                1024, min(65_536, int(payload.get("max_receipt_chars", 16_384)))
            ),
            max_model_snapshot_bytes=max(
                1, int(payload.get("max_model_snapshot_bytes", 50_000_000_000))
            ),
            max_repository_archive_bytes=max(
                1, int(payload.get("max_repository_archive_bytes", 2_000_000_000))
            ),
            max_repository_expanded_bytes=max(
                1, int(payload.get("max_repository_expanded_bytes", 2_000_000_000))
            ),
            model_cache_root=str(
                payload.get("model_cache_root", defaults.model_cache_root)
                or defaults.model_cache_root
            ),
            repository_cache_root=str(
                payload.get("repository_cache_root", defaults.repository_cache_root)
                or defaults.repository_cache_root
            ),
            huggingface_route_status=_coerce_choice(
                payload.get("huggingface_route_status"), route_status, "unavailable"
            ),
            github_api_route_status=_coerce_choice(
                payload.get("github_api_route_status"), route_status, "unavailable"
            ),
            github_archive_route_status=_coerce_choice(
                payload.get("github_archive_route_status"), route_status, "unavailable"
            ),
            github_ssh_route_status=_coerce_choice(
                payload.get("github_ssh_route_status"), route_status, "unavailable"
            ),
            github_https_git_route_status=_coerce_choice(
                payload.get("github_https_git_route_status"),
                route_status,
                "unavailable",
            ),
            github_api_endpoint=str(
                payload.get("github_api_endpoint", "https://api.github.com")
                or "https://api.github.com"
            ),
            github_archive_endpoint=str(
                payload.get("github_archive_endpoint", "https://codeload.github.com")
                or "https://codeload.github.com"
            ),
            github_ssh_host_alias=str(payload.get("github_ssh_host_alias", "") or ""),
            github_ssh_identity_path=str(
                payload.get("github_ssh_identity_path", "") or ""
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ResourceRuntimeConfig:
    mirrors: dict[str, str] = field(default_factory=dict)
    env_vars: dict[str, str] = field(default_factory=dict)
    materialization: ResourceMaterializationConfig = field(
        default_factory=ResourceMaterializationConfig
    )

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ResourceRuntimeConfig":
        payload = data or {}
        return cls(
            mirrors=_coerce_string_mapping(payload.get("mirrors")),
            env_vars=_coerce_string_mapping(payload.get("env_vars")),
            materialization=ResourceMaterializationConfig.from_dict(
                payload.get("materialization")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mirrors": dict(self.mirrors),
            "env_vars": dict(self.env_vars),
            "materialization": self.materialization.to_dict(),
        }


@dataclass(frozen=True)
class EnvironmentConfig:
    default_provider: str = "host_local"
    local: LocalEnvironmentConfig = field(default_factory=LocalEnvironmentConfig)
    docker: DockerEnvironmentConfig = field(default_factory=DockerEnvironmentConfig)
    ssh_targets: list[SSHRemoteTargetConfig] = field(default_factory=list)
    routing: EnvironmentRoutingConfig = field(default_factory=EnvironmentRoutingConfig)
    lsp: LSPRuntimeConfig = field(default_factory=LSPRuntimeConfig)
    proxy_env: dict[str, str] = field(default_factory=dict)
    resources: ResourceRuntimeConfig = field(default_factory=ResourceRuntimeConfig)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "EnvironmentConfig":
        payload = data or {}
        ssh_payload = (
            payload.get("ssh", {}) if isinstance(payload.get("ssh"), dict) else {}
        )
        targets = (
            ssh_payload.get("targets", [])
            if isinstance(ssh_payload.get("targets"), list)
            else payload.get("ssh_targets", [])
        )
        return cls(
            default_provider=str(
                payload.get("default_provider", "host_local") or "host_local"
            ),
            local=LocalEnvironmentConfig.from_dict(payload.get("local")),
            docker=DockerEnvironmentConfig.from_dict(payload.get("docker")),
            ssh_targets=[
                SSHRemoteTargetConfig.from_dict(item)
                for item in targets
                if isinstance(item, dict)
                and str(item.get("name", "") or "").strip()
                and str(item.get("host", "") or "").strip()
            ],
            routing=EnvironmentRoutingConfig.from_dict(payload.get("routing")),
            lsp=LSPRuntimeConfig.from_dict(payload.get("lsp")),
            proxy_env=_coerce_string_mapping(payload.get("proxy_env")),
            resources=ResourceRuntimeConfig.from_dict(payload.get("resources")),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "default_provider": self.default_provider,
            "local": self.local.to_dict(),
            "docker": self.docker.to_dict(),
            "ssh": {"targets": [item.to_dict() for item in self.ssh_targets]},
            "routing": self.routing.to_dict(),
            "lsp": self.lsp.to_dict(),
            "proxy_env": dict(self.proxy_env),
            "resources": self.resources.to_dict(),
        }


def _coerce_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _coerce_string_mapping(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(key): str(item) for key, item in value.items()}


def _coerce_choice(value: Any, choices: set[str], default: str) -> str:
    normalized = str(value or default).strip().lower()
    return normalized if normalized in choices else default


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _optional_nonnegative_int(
    value: Any,
    *,
    field_name: str = "value",
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field_name} must be a non-negative integer")
    if isinstance(value, str) and not value.strip().isdigit():
        raise ValueError(f"{field_name} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a non-negative integer") from exc
    if parsed < 0:
        raise ValueError(f"{field_name} must be a non-negative integer")
    return parsed


def _required_positive_int(value: Any, *, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ValueError(f"{field_name} must be a positive integer")
    if isinstance(value, str) and not value.strip().isdigit():
        raise ValueError(f"{field_name} must be a positive integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a positive integer") from exc
    if parsed < 1:
        raise ValueError(f"{field_name} must be at least 1")
    return parsed


def _optional_lower_string(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().lower()
    return normalized or None


def _coerce_web_search_user_location(value: Any) -> tuple[tuple[str, str], ...]:
    if isinstance(value, dict):
        payload = value
    elif isinstance(value, (list, tuple)):
        try:
            payload = dict(value)
        except (TypeError, ValueError):
            return ()
    else:
        return ()
    location_type = (
        str(payload.get("type", "approximate") or "approximate").strip().lower()
    )
    if location_type != "approximate":
        raise ValueError("web_search_user_location.type must be approximate")
    allowed_keys = ("country", "region", "city", "timezone")
    return tuple(
        (key, str(payload[key]).strip())
        for key in allowed_keys
        if payload.get(key) is not None and str(payload[key]).strip()
    )


def _coerce_modality_tuple(value: Any) -> tuple[str, ...]:
    raw_items = value if isinstance(value, (list, tuple)) else (value,)
    modalities: list[str] = []
    seen: set[str] = set()
    for item in raw_items:
        normalized = str(item or "").strip().lower()
        if normalized not in {"text", "image"} or normalized in seen:
            continue
        seen.add(normalized)
        modalities.append(normalized)
    return tuple(modalities or ["text"])


def _coerce_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().casefold()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    return bool(value)


@dataclass(frozen=True)
class RuntimeInteractionConfig:
    allow_human_question: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "RuntimeInteractionConfig":
        payload = data if isinstance(data, dict) else {}
        return cls(
            allow_human_question=_coerce_bool(
                payload.get("allow_human_question"),
                default=False,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class PlanModeRuntimeConfig:
    enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PlanModeRuntimeConfig":
        payload = data if isinstance(data, dict) else {}
        unknown_fields = sorted(set(payload).difference({"enabled"}))
        if unknown_fields:
            raise ValueError(
                "runtime.plan_mode contains unsupported fields: "
                f"{', '.join(unknown_fields)}"
            )
        return cls(
            enabled=_coerce_bool(payload.get("enabled"), default=False),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NativeConversationCompactionConfig:
    enabled: bool = True
    unknown_model_budget_policy: str = "require_explicit_fallback"
    auto_compact_limit_tokens_override: int | None = None
    response_reserve_tokens_override: int | None = None

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any] | None,
    ) -> "NativeConversationCompactionConfig":
        payload = data if isinstance(data, dict) else {}
        allowed = {
            "enabled",
            "unknown_model_budget_policy",
            "auto_compact_limit_tokens_override",
            "response_reserve_tokens_override",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                "runtime.native_conversation.conversation_compaction contains "
                f"unsupported fields: {', '.join(unknown)}"
            )
        unknown_policy = str(
            payload.get(
                "unknown_model_budget_policy",
                "require_explicit_fallback",
            )
            or "require_explicit_fallback"
        ).strip()
        if unknown_policy != "require_explicit_fallback":
            raise ValueError(
                "runtime.native_conversation.conversation_compaction."
                "unknown_model_budget_policy must be require_explicit_fallback"
            )
        return cls(
            enabled=bool(payload.get("enabled", True)),
            unknown_model_budget_policy=unknown_policy,
            auto_compact_limit_tokens_override=_optional_positive_int(
                payload.get("auto_compact_limit_tokens_override")
            ),
            response_reserve_tokens_override=_optional_positive_int(
                payload.get("response_reserve_tokens_override")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class NativeConversationRuntimeConfig:
    max_inline_text_bytes: int = 16_384
    max_metadata_bytes: int = 4_096
    max_opaque_provider_bytes: int = 8_192
    max_page_bytes: int = 32_768
    max_trace_inline_bytes: int = 4_096
    lookup_cache_items: int = DEFAULT_CONVERSATION_LOOKUP_CACHE_ITEMS
    page_item_limit: int = DEFAULT_CONVERSATION_PAGE_ITEMS
    conversation_compaction: NativeConversationCompactionConfig = field(
        default_factory=NativeConversationCompactionConfig
    )

    @classmethod
    def from_dict(
        cls, data: dict[str, Any] | None
    ) -> "NativeConversationRuntimeConfig":
        payload = data if isinstance(data, dict) else {}
        defaults = cls()
        allowed = {
            "max_inline_text_bytes",
            "max_metadata_bytes",
            "max_opaque_provider_bytes",
            "max_page_bytes",
            "max_trace_inline_bytes",
            "lookup_cache_items",
            "page_item_limit",
            "conversation_compaction",
        }
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(
                "runtime.native_conversation contains unsupported fields: "
                f"{', '.join(unknown)}"
            )

        def positive_int(key: str) -> int:
            return max(
                1,
                int(payload.get(key, getattr(defaults, key)) or getattr(defaults, key)),
            )

        max_page_bytes = positive_int("max_page_bytes")
        if max_page_bytes < 1024:
            raise ValueError(
                "runtime.native_conversation.max_page_bytes must be at least 1024"
            )
        return cls(
            max_inline_text_bytes=positive_int("max_inline_text_bytes"),
            max_metadata_bytes=positive_int("max_metadata_bytes"),
            max_opaque_provider_bytes=positive_int("max_opaque_provider_bytes"),
            max_page_bytes=max_page_bytes,
            max_trace_inline_bytes=positive_int("max_trace_inline_bytes"),
            lookup_cache_items=positive_int("lookup_cache_items"),
            page_item_limit=positive_int("page_item_limit"),
            conversation_compaction=NativeConversationCompactionConfig.from_dict(
                payload.get("conversation_compaction")
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ProcessOwnerRuntimeConfig:
    owner_daemon_idle_grace_seconds: float = PROCESS_OWNER_DAEMON_IDLE_GRACE_SECONDS

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> 'ProcessOwnerRuntimeConfig':
        payload = data if isinstance(data, dict) else {}
        allowed = {'owner_daemon_idle_grace_seconds'}
        unknown = sorted(set(payload) - allowed)
        if unknown:
            raise ValueError(f'runtime.process_owner contains unknown fields: {unknown}')

        def non_negative(name: str, default: float) -> float:
            value = float(payload.get(name, default))
            if value < 0:
                raise ValueError(f'runtime.process_owner.{name} must be non-negative')
            return value

        defaults = cls()
        return cls(
            owner_daemon_idle_grace_seconds=non_negative(
                'owner_daemon_idle_grace_seconds', defaults.owner_daemon_idle_grace_seconds
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuntimeConfig:
    workspace_dir: str
    output_dir: str
    project_root: str = ""
    default_profile: str = "research"
    review_profile: str = "auto"
    interaction: RuntimeInteractionConfig = field(
        default_factory=RuntimeInteractionConfig
    )
    plan_mode: PlanModeRuntimeConfig = field(default_factory=PlanModeRuntimeConfig)
    native_conversation: NativeConversationRuntimeConfig = field(
        default_factory=NativeConversationRuntimeConfig
    )
    process_owner: ProcessOwnerRuntimeConfig = field(
        default_factory=ProcessOwnerRuntimeConfig
    )
    permissions: RuntimePermissionsConfig = field(default_factory=RuntimePermissionsConfig)
    web_tools: WebToolsConfig = field(default_factory=WebToolsConfig)
    mcp_servers: list[MCPServerConfig] = field(default_factory=list)
    notebook_tools: NotebookToolsConfig = field(default_factory=NotebookToolsConfig)
    image_tools: ImageToolsConfig = field(default_factory=ImageToolsConfig)
    internal_memory: InternalMemoryConfig = field(default_factory=InternalMemoryConfig)
    external_knowledge: ExternalKnowledgeConfig = field(
        default_factory=ExternalKnowledgeConfig
    )
    hooks: HookRegistryConfig = field(default_factory=HookRegistryConfig)
    builtin_hooks: BuiltinHooksConfig = field(default_factory=BuiltinHooksConfig)
    skill_trust: SkillTrustConfig = field(default_factory=SkillTrustConfig)
    extension_trust: ExtensionTrustConfig = field(default_factory=ExtensionTrustConfig)
    environment: EnvironmentConfig = field(default_factory=EnvironmentConfig)
    ui: RuntimeUIConfig = field(default_factory=RuntimeUIConfig)
    subagent_enabled: bool = True
    subagent_max_count: int = 8

    def __post_init__(self) -> None:
        runtime_profile_selection_from_value(self.default_profile)
        review_profile_selection_from_value(self.review_profile)
        if type(self.subagent_max_count) is not int or self.subagent_max_count < 1:
            raise ValueError("runtime.subagent_max_count must be a positive integer")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RuntimeConfig":
        if "tool_permissions" in data:
            raise ValueError(
                "runtime.tool_permissions was removed; configure runtime.permissions instead"
            )
        plan_mode = PlanModeRuntimeConfig.from_dict(data.get("plan_mode"))
        if plan_mode.enabled:
            raise ValueError("runtime.plan_mode is not available in the beta release")
        return cls(
            workspace_dir=str(data["workspace_dir"]),
            output_dir=str(data["output_dir"]),
            project_root=str(data.get("project_root", "") or ""),
            default_profile=runtime_profile_selection_from_value(
                data.get("default_profile", "research")
            ).value,
            review_profile=review_profile_selection_from_value(
                data.get("review_profile", "auto")
            ),
            interaction=RuntimeInteractionConfig.from_dict(data.get("interaction")),
            plan_mode=plan_mode,
            native_conversation=NativeConversationRuntimeConfig.from_dict(
                data.get("native_conversation")
            ),
            process_owner=ProcessOwnerRuntimeConfig.from_dict(data.get('process_owner')),
            permissions=RuntimePermissionsConfig.from_dict(data.get("permissions")),
            web_tools=WebToolsConfig.from_dict(data.get("web_tools")),
            mcp_servers=[
                MCPServerConfig.from_dict(item)
                for item in data.get("mcp_servers", [])
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            ],
            notebook_tools=NotebookToolsConfig.from_dict(data.get("notebook_tools")),
            image_tools=ImageToolsConfig.from_dict(data.get("image_tools")),
            internal_memory=InternalMemoryConfig.from_dict(data.get("internal_memory")),
            external_knowledge=ExternalKnowledgeConfig.from_dict(
                data.get("external_knowledge")
            ),
            hooks=HookRegistryConfig.from_dict(data.get("hooks")),
            builtin_hooks=BuiltinHooksConfig.from_dict(data.get("builtin_hooks")),
            skill_trust=SkillTrustConfig.from_dict(data.get("skill_trust")),
            extension_trust=ExtensionTrustConfig.from_dict(data.get("extension_trust")),
            environment=EnvironmentConfig.from_dict(data.get("environment")),
            ui=RuntimeUIConfig.from_dict(data.get("ui")),
            subagent_enabled=bool(data.get("subagent_enabled", True)),
            subagent_max_count=data.get("subagent_max_count", 8),
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["interaction"] = self.interaction.to_dict()
        payload["native_conversation"] = self.native_conversation.to_dict()
        payload['process_owner'] = self.process_owner.to_dict()
        payload["permissions"] = self.permissions.to_dict()
        payload["web_tools"] = self.web_tools.to_dict()
        payload["mcp_servers"] = [item.to_dict() for item in self.mcp_servers]
        payload["notebook_tools"] = self.notebook_tools.to_dict()
        payload["image_tools"] = self.image_tools.to_dict()
        payload["internal_memory"] = self.internal_memory.to_dict()
        payload["external_knowledge"] = self.external_knowledge.to_dict()
        payload["hooks"] = self.hooks.to_dict()
        payload["builtin_hooks"] = self.builtin_hooks.to_dict()
        payload["skill_trust"] = self.skill_trust.to_dict()
        payload["extension_trust"] = self.extension_trust.to_dict()
        payload["environment"] = self.environment.to_dict()
        payload["ui"] = self.ui.to_dict()
        return payload


@dataclass(frozen=True)
class AgentInstanceConfig:
    id: str
    role: str
    name: str
    llm: str = "default"
    enabled: bool = True
    options: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        *,
        default_id: str | None = None,
        default_role: str | None = None,
    ) -> "AgentInstanceConfig":
        known_keys = {"id", "role", "name", "llm", "enabled", "options"}
        extra_options = {
            key: value for key, value in data.items() if key not in known_keys
        }
        explicit_options = data.get("options", {})
        if explicit_options is None:
            explicit_options = {}
        if not isinstance(explicit_options, dict):
            raise ValueError("Agent options must be a mapping.")
        options = {**extra_options, **explicit_options}
        agent_id = str(
            data.get("id") or default_id or data.get("role") or default_role or "agent"
        )
        role = str(data.get("role") or default_role or agent_id)
        return cls(
            id=agent_id,
            role=role,
            name=str(data.get("name") or agent_id),
            llm=str(data.get("llm", "default")),
            enabled=bool(data.get("enabled", True)),
            options=options,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "llm": self.llm,
            "enabled": self.enabled,
            "options": dict(self.options),
        }


@dataclass(frozen=True)
class AgentsConfig:
    pipeline: list[AgentInstanceConfig]

    @classmethod
    def default(cls) -> "AgentsConfig":
        return cls(
            pipeline=[
                AgentInstanceConfig(
                    id="core", role="core", name="OwnPI-Core", llm="default"
                ),
                AgentInstanceConfig(
                    id="reviewer",
                    role="reviewer",
                    name="OwnPI-Reviewer",
                    llm="default",
                ),
            ]
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "AgentsConfig":
        if "pipeline" in data:
            raw_pipeline = data.get("pipeline", [])
            if not isinstance(raw_pipeline, list):
                raise ValueError("agents.pipeline must be a list.")
            pipeline = [
                AgentInstanceConfig.from_dict(item, default_id=f"agent_{index + 1}")
                for index, item in enumerate(raw_pipeline)
            ]
        else:
            pipeline = []
        if not pipeline:
            raise ValueError(
                "At least one enabled agent must be configured in agents.pipeline."
            )
        return cls(pipeline=pipeline)

    def to_dict(self) -> dict[str, Any]:
        return {"pipeline": [item.to_dict() for item in self.pipeline]}

    def get_enabled_pipeline(self) -> list[AgentInstanceConfig]:
        return [item for item in self.pipeline if item.enabled]


@dataclass(frozen=True)
class UserConfig:
    llms: dict[str, LLMConfig]
    runtime: RuntimeConfig
    agents: AgentsConfig
    selected_llm_profile: str | None = None
    fallback_llm_profile: str | None = None

    @classmethod
    def from_dict(
        cls, data: dict[str, Any], *, llm_profile: str | None = None
    ) -> "UserConfig":
        raw_llms = data.get("llms")
        raw_llm_profiles = data.get("llm_profiles")
        selected_profile = _resolve_llm_profile_name(data.get("llm"), llm_profile)
        fallback_profile = _llm_profile_field(data.get("llm"), "fallback_profile")
        if raw_llms is not None:
            if not isinstance(raw_llms, dict) or not raw_llms:
                raise ValueError("llms must be a non-empty mapping when provided.")
            llms = {
                str(name): LLMConfig.from_dict(cfg) for name, cfg in raw_llms.items()
            }
            if selected_profile:
                llms = _bind_selected_llm_profile(llms, selected_profile)
        elif isinstance(raw_llm_profiles, dict) and raw_llm_profiles:
            profiles = {
                str(name): LLMConfig.from_dict(cfg)
                for name, cfg in raw_llm_profiles.items()
                if isinstance(cfg, dict)
            }
            if not profiles:
                raise ValueError(
                    "llm_profiles must contain at least one profile mapping."
                )
            if not selected_profile:
                available = ", ".join(sorted(profiles))
                raise ValueError(
                    "Missing LLM profile selection. Set `llm.profile`, "
                    "`--llm-profile`, or OWNPI_LLM_PROFILE. "
                    f"Available profiles: {available}."
                )
            llms = _bind_selected_llm_profile(
                profiles,
                selected_profile,
            )
        elif "llm" in data:
            llms = {"default": LLMConfig.from_dict(data["llm"])}
        else:
            raise ValueError(
                "User config must include `llm`, `llms`, or `llm_profiles`."
            )
        return cls(
            llms=llms,
            runtime=RuntimeConfig.from_dict(data["runtime"]),
            agents=AgentsConfig.from_dict(data.get("agents", AgentsConfig.default().to_dict())),
            selected_llm_profile=selected_profile,
            fallback_llm_profile=fallback_profile,
        )

    @property
    def default_llm_name(self) -> str:
        if "default" in self.llms:
            return "default"
        return next(iter(self.llms))

    def resolve_llm_name(self, name: str | None = None) -> str:
        if name is None or name == "default":
            return self.default_llm_name
        if name in self.llms:
            return name
        available = ", ".join(sorted(self.llms))
        raise ValueError(
            f"Unknown llm profile: {name!r}. Available profiles: {available}."
        )

    @property
    def llm(self) -> LLMConfig:
        return self.get_llm_config()

    def get_llm_config(self, name: str | None = None) -> LLMConfig:
        return self.llms[self.resolve_llm_name(name)]

    @property
    def selectable_llm_profiles(self) -> tuple[str, ...]:
        if not (self.selected_llm_profile or self.fallback_llm_profile):
            return tuple(sorted(self.llms))
        profile_names = [
            name
            for name in self.llms
            if name not in {"default", "reviewer_model"}
            and not name.startswith("reviewer_")
        ]
        return tuple(sorted(profile_names or self.llms))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "llms": {name: cfg.to_dict() for name, cfg in self.llms.items()},
            "runtime": self.runtime.to_dict(),
            "agents": self.agents.to_dict(),
        }
        if self.selected_llm_profile or self.fallback_llm_profile:
            llm_selector: dict[str, str] = {}
            if self.selected_llm_profile:
                llm_selector["profile"] = self.selected_llm_profile
            if self.fallback_llm_profile:
                llm_selector["fallback_profile"] = self.fallback_llm_profile
            payload["llm"] = llm_selector
        return payload


def _llm_profile_field(raw_llm: Any, key: str) -> str | None:
    if not isinstance(raw_llm, dict):
        return None
    value = raw_llm.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_llm_profile_name(raw_llm: Any, explicit_profile: str | None) -> str | None:
    if explicit_profile:
        return explicit_profile.strip() or None
    for env_name in ("OWNPI_LLM_PROFILE",):
        env_profile = os.getenv(env_name, "").strip()
        if env_profile:
            return env_profile
    return _llm_profile_field(raw_llm, "profile")


def _bind_selected_llm_profile(
    profiles: dict[str, LLMConfig],
    selected_profile: str,
    *,
    reviewer_profile: str | None = None,
) -> dict[str, LLMConfig]:
    if selected_profile not in profiles:
        available = ", ".join(sorted(profiles))
        raise ValueError(
            f"Unknown llm profile: {selected_profile!r}. Available profiles: {available}."
        )
    selected_config = profiles[selected_profile]
    rebound = dict(profiles)
    rebound["default"] = selected_config
    rebound["reviewer_model"] = selected_config
    return rebound
