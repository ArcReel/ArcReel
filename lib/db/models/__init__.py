"""ORM model exports."""

from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.models.api_call import ApiCall
from lib.db.models.api_key import ApiKey
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.asset_library import AssetLibraryAccount, AssetLibraryBinding, ProviderAssetLink
from lib.db.models.builtin_provider_model import BuiltinProviderModelState
from lib.db.models.catalog_official_sync import CatalogOfficialSync
from lib.db.models.catalog_user_entry import CatalogUserEntry
from lib.db.models.config import ProviderConfig, SystemSetting
from lib.db.models.credential import ProviderCredential
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
from lib.db.models.market_installation import MarketInstallation
from lib.db.models.market_source import MarketSource
from lib.db.models.market_submission import MarketSubmission
from lib.db.models.object_storage import FormalObject, FormalObjectRef, ObjectStorageConnection, RelayObject
from lib.db.models.session import AgentSession
from lib.db.models.session_event import AgentSessionEventLogEntry
from lib.db.models.session_message_link import AgentSessionUserMessageLink
from lib.db.models.task import BatchTask, GenerationBatch, Task, WorkerLease
from lib.db.models.user import User

__all__ = [
    "AgentAnthropicCredential",
    "AgentSession",
    "AgentSessionEventLogEntry",
    "AgentSessionUserMessageLink",
    "ApiCall",
    "ApiKey",
    "Asset",
    "AssetDerivative",
    "AssetLibraryAccount",
    "AssetLibraryBinding",
    "BatchTask",
    "BuiltinProviderModelState",
    "CatalogOfficialSync",
    "CatalogUserEntry",
    "CustomEndpoint",
    "CustomProvider",
    "CustomProviderModel",
    "FormalObject",
    "FormalObjectRef",
    "GenerationBatch",
    "MarketInstallation",
    "MarketSource",
    "MarketSubmission",
    "ObjectStorageConnection",
    "ProviderAssetLink",
    "ProviderConfig",
    "ProviderCredential",
    "RelayObject",
    "SystemSetting",
    "Task",
    "User",
    "WorkerLease",
    "register_models",
]


def register_models() -> None:
    """Register all ORM models in this package with ``Base.metadata``.

    Registration happens when this module is imported (class definitions register models).
    This function does no additional work; calling it makes the side-effect import explicit at the call site.
    """
