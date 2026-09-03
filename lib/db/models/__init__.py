"""ORM model exports."""

from lib.db.models.agent_credential import AgentAnthropicCredential
from lib.db.models.api_call import ApiCall
from lib.db.models.api_key import ApiKey
from lib.db.models.asset import Asset, AssetDerivative
from lib.db.models.config import ProviderConfig, SystemSetting
from lib.db.models.credential import ProviderCredential
from lib.db.models.custom_endpoint import CustomEndpoint
from lib.db.models.custom_provider import CustomProvider, CustomProviderModel
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
    "BatchTask",
    "CustomEndpoint",
    "CustomProvider",
    "CustomProviderModel",
    "GenerationBatch",
    "ProviderConfig",
    "ProviderCredential",
    "SystemSetting",
    "Task",
    "User",
    "WorkerLease",
    "register_models",
]


def register_models() -> None:
    """把本包内全部 ORM 模型登记到 ``Base.metadata``。

    登记发生在 import 本模块时（模型类定义即注册），本函数不做额外工作；
    调用它是为了让「因副作用而 import」在调用点显式可见。
    """
