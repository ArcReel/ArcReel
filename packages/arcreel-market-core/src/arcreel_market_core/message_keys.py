"""本包能产出的全部消息键。

本包不携带翻译目录，诊断只带 ``key + params``；渲染成自然语言的一方（ArcReel 应用、官方服务的
客户端）须为这里的每个键备好各语言文案。集合按码表与键表派生，新增码或键即自动进入。
"""

from __future__ import annotations

from arcreel_market_core.comfyui.validator import REMOVED_FIELD_REASONS as COMFYUI_REMOVED_FIELD_REASONS
from arcreel_market_core.definition_diagnostics import DefinitionErrorCode
from arcreel_market_core.definition_diagnostics import message_key as definition_message_key
from arcreel_market_core.definition_schema_errors import SCHEMA_CONSTRAINT_KEYS
from arcreel_market_core.endpoint_definition.validator import REMOVED_FIELD_REASONS as DECLARATIVE_REMOVED_FIELD_REASONS
from arcreel_market_core.market.generate import META_NOT_OBJECT_KEY
from arcreel_market_core.market.issues import MarketIssueCode
from arcreel_market_core.market.issues import message_key as market_message_key
from arcreel_market_core.validation_messages import LITERAL_KEY

DEFINITION_MESSAGE_KEYS: frozenset[str] = frozenset(
    {definition_message_key(code) for code in DefinitionErrorCode}
    | set(DECLARATIVE_REMOVED_FIELD_REASONS.values())
    | set(COMFYUI_REMOVED_FIELD_REASONS.values())
    | set(SCHEMA_CONSTRAINT_KEYS)
)

MARKET_MESSAGE_KEYS: frozenset[str] = frozenset(
    {market_message_key(code) for code in MarketIssueCode}
    | {
        META_NOT_OBJECT_KEY,
        "val_market_cli_check_failed",
        "val_market_cli_check_passed",
        "val_market_cli_generate_failed",
        "val_market_cli_generate_written",
    }
)

MESSAGE_KEYS: frozenset[str] = DEFINITION_MESSAGE_KEYS | MARKET_MESSAGE_KEYS | {LITERAL_KEY}
