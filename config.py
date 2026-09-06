"""Configuration: ~/.hermes/config.yaml (single source) + env overrides."""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

# Single configuration source: the Hermes agent config (Hermes standard per
# docs: memory.provider etc. live in ~/.hermes/config.yaml).
HERMES_CONFIG_PATH = "~/.hermes/config.yaml"

# Test hook: allow overriding the config location without touching env/global state
_override_config_path: Optional[str] = None

# Literal group-type values allowed in the mapping
GROUP_TYPES = ("team", "organization", "permission", "ignore")

# Prefix for conversation memory tags (used in Talk room descriptions only —
# Deck board titles are user-owned and NOT used for memory tagging)
MEMORY_TAG_PREFIX = "[memory:"

DEFAULT_SCOPE = "personal"  # fallback_scope values: "personal" | None


@dataclass
class GroupMapping:
    """Semantic type of one Nextcloud group."""
    name: str
    type: str = "ignore"  # team | organization | permission | ignore
    scope: Optional[str] = None  # explicit scope override, e.g. "team:project-x"


@dataclass
class MemoryConfig:
    personal: bool = True
    teams: bool = True
    organization: bool = True
    # Deterministic conversation->scope mapping (exact match on conversation_id)
    conversation_scopes: Dict[str, str] = field(default_factory=dict)
    # Scope when no conversation mapping matches (e.g. new untagged rooms / DMs)
    fallback_scope: str = DEFAULT_SCOPE


@dataclass
class HonchoConfig:
    enabled: bool = False
    workspace_id: Optional[str] = None  # defaults to organization


@dataclass
class PluginConfig:
    organization: Optional[str] = None
    group_mapping: Dict[str, GroupMapping] = field(default_factory=dict)
    memory: MemoryConfig = field(default_factory=MemoryConfig)
    honcho: HonchoConfig = field(default_factory=HonchoConfig)
    adapter_secret: Optional[str] = None  # from env only, never from YAML
    debug: bool = False
    fallback_user: Optional[str] = None  # from env only


_config: Optional[PluginConfig] = None


def _expand(path: str) -> Path:
    return Path(os.path.expanduser(path))


def _apply_mapping_section(data: Dict[str, Any], cfg: PluginConfig) -> None:
    cfg.organization = data.get("organization") or cfg.organization

    mapping = data.get("group_mapping") or {}
    if isinstance(mapping, dict):
        for name, spec in mapping.items():
            if isinstance(spec, dict):
                gtype = str(spec.get("type") or "ignore").strip().lower()
                if gtype not in GROUP_TYPES:
                    logger.warning("[X-On-Behalf] Unbekannter group_mapping-Typ '%s' für Gruppe '%s' — ignoriert.", gtype, name)
                    gtype = "ignore"
                cfg.group_mapping[str(name).strip().lower()] = GroupMapping(
                    name=str(name).strip(),
                    type=gtype,
                    scope=spec.get("scope"),
                )
            else:
                # shorthand: `developers: team`
                gtype = str(spec).strip().lower()
                if gtype not in GROUP_TYPES:
                    logger.warning("[X-On-Behalf] Unbekannter group_mapping-Typ '%s' für Gruppe '%s' — ignoriert.", gtype, name)
                    gtype = "ignore"
                cfg.group_mapping[str(name).strip().lower()] = GroupMapping(name=str(name).strip(), type=gtype)

    memory = data.get("memory") or {}
    if isinstance(memory, dict):
        cfg.memory = MemoryConfig(
            personal=bool(memory.get("personal", True)),
            teams=bool(memory.get("teams", True)),
            organization=bool(memory.get("organization", True)),
            conversation_scopes={str(k): str(v) for k, v in (memory.get("conversation_scopes") or {}).items()}
            if isinstance(memory.get("conversation_scopes"), dict) else {},
            fallback_scope=str(memory.get("fallback_scope") or DEFAULT_SCOPE),
        )

    honcho = data.get("honcho") or {}
    if isinstance(honcho, dict):
        cfg.honcho = HonchoConfig(
            enabled=bool(honcho.get("enabled", False)),
            workspace_id=honcho.get("workspace_id") or None,
        )


def _load_yaml_file(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore[import-untyped]

        data = yaml.safe_load(text) or {}
        logger.info("[X-On-Behalf] Konfiguration geladen: %s", path)
        return data if isinstance(data, dict) else {}
    except ImportError:
        # PyYAML nicht verfügbar → minimaler Fallback-Parser für die flache
        # x_on_behalf-Struktur (Mappings/Dicts/Einrückung, keine erweiterte
        # YAML-Syntax). Produktiv ist PyYAML vorhanden; der Fallback hält die
        # Plugin-Funktionen auch ohne Dependency lauffähig.
        logger.info("[X-On-Behalf] PyYAML nicht installiert — Fallback-Parser für %s.", path)
        data = _parse_simple_yaml(text)
        logger.info("[X-On-Behalf] Konfiguration geladen (Fallback-Parser): %s", path)
        return data
    except Exception as exc:
        logger.warning("[X-On-Behalf] Konfiguration %s konnte nicht geladen werden: %s", path, exc)
    return None


def _parse_simple_yaml(text: str) -> Dict[str, Any]:
    """Minimal YAML subset parser: nested dicts, scalars, inline {key: value}.

    Supports what the x_on_behalf section needs: two-level nesting, inline
    flow mappings (`{type: team}`), quoted strings, comments. Lists are not
    used in this config format.
    """
    import re as _re

    root: Dict[str, Any] = {}
    stack: list[tuple[int, Dict[str, Any]]] = [(-1, root)]

    def _scalar(raw: str) -> Any:
        raw = raw.strip()
        if not raw:
            return None
        if raw.startswith(('"', "'")) and raw.endswith(raw[0]) and len(raw) >= 2:
            return raw[1:-1]
        if raw.startswith("{") and raw.endswith("}"):
            inner: Dict[str, Any] = {}
            for part in _re.split(r",(?![^{}]*\})", raw[1:-1]):
                if ":" in part:
                    k, _, v = part.partition(":")
                    inner[k.strip().strip('"\'')] = _scalar(v)
            return inner
        low = raw.lower()
        if low in ("true", "yes", "on"):
            return True
        if low in ("false", "no", "off"):
            return False
        if low in ("null", "none", "~"):
            return None
        try:
            return int(raw)
        except ValueError:
            return raw

    for raw_line in text.splitlines():
        stripped = raw_line.split("#", 1)[0].rstrip()
        if not stripped.strip():
            continue
        indent = len(stripped) - len(stripped.lstrip())
        key, sep, value_part = _split_key_value(stripped.strip())
        if not sep:
            continue
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value_part.strip():
            parent[key] = _scalar(value_part)
        else:
            child: Dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))

    return root


def _split_key_value(line: str) -> tuple[str, str, str]:
    """Split 'key: value' at the first colon OUTSIDE quotes.

    Handles quoted keys containing colons: `"deck:board:3": team:x` →
    key='deck:board:3', value=' team:x'.
    """
    in_quote: Optional[str] = None
    for i, ch in enumerate(line):
        if in_quote:
            if ch == in_quote:
                in_quote = None
        elif ch in ('"', "'"):
            in_quote = ch
        elif ch == ":":
            # Check it's not part of a quoted key that continues
            key_raw = line[:i].strip()
            if key_raw.startswith(('"', "'")) and not (len(key_raw) >= 2 and key_raw.endswith(key_raw[0])):
                continue  # colon inside an open quoted key
            return key_raw.strip('"\''), ":", line[i + 1:]
    return line.strip(), "", ""


def load_config(force_reload: bool = False) -> PluginConfig:
    """Load config from the `x_on_behalf:` section of the Hermes agent config
    (~/.hermes/config.yaml), then apply env overrides. This is the single
    configuration source — there is no separate plugin YAML file."""
    global _config
    if _config is not None and not force_reload:
        return _config

    cfg = PluginConfig()

    # x_on_behalf section in the Hermes agent config (~/.hermes/config.yaml)
    path = _expand(_override_config_path or os.getenv("HERMES_X_ON_BEHALF_CONFIG", "").strip() or HERMES_CONFIG_PATH)
    data = _load_yaml_file(path)
    if data and isinstance(data.get("x_on_behalf"), dict):
        _apply_mapping_section(data["x_on_behalf"], cfg)
    elif data is not None:
        logger.info("[X-On-Behalf] Kein 'x_on_behalf:'-Abschnitt in %s gefunden — Defaults aktiv.", path)

    # Env overrides (flags only; scope mapping stays in the YAML section)
    cfg.debug = os.getenv("HERMES_X_ON_BEHALF_DEBUG", "").strip().lower() in ("1", "true", "yes", "on")
    cfg.fallback_user = os.getenv("MCP_IDENTITY_FALLBACK_USER", "").strip() or None
    cfg.adapter_secret = os.getenv("HERMES_X_ON_BEHALF_ADAPTER_SECRET", "").strip() or None

    _config = cfg
    return cfg


def reset_config_cache() -> None:
    """Test helper: forget the cached config so the next load re-reads env/files."""
    global _config
    _config = None
