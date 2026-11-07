"""All message keys this package can produce.

This package carries no translation catalog; diagnostics contain only ``key + params``. Natural-language renderers (the ArcReel app and official service
clients) must supply copy for every key in each language. The set derives from code and key tables, automatically including new codes and keys.
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

#: All message keys for endpoint diagnostics: diagnostic codes, destinations for removed fields, and jsonschema constraint details.
DEFINITION_MESSAGE_KEYS: frozenset[str] = frozenset(
    {definition_message_key(code) for code in DefinitionErrorCode}
    | set(DECLARATIVE_REMOVED_FIELD_REASONS.values())
    | set(COMFYUI_REMOVED_FIELD_REASONS.values())
    | set(SCHEMA_CONSTRAINT_KEYS)
)

#: All message keys used by market source diagnostics.
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

#: All message keys this package can produce, including ``LITERAL_KEY`` for passing finished text through.
MESSAGE_KEYS: frozenset[str] = DEFINITION_MESSAGE_KEYS | MARKET_MESSAGE_KEYS | {LITERAL_KEY}
