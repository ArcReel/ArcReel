"""调用通道与供应商素材引用的交界：通道实际使用的推理凭证、可接受素材引用的图片槽位与输入图片被拒。

素材库按推理凭证的显式绑定决定一次调用能用哪个范围的供应商素材（见 ``lib.asset_library.bindings``），
所以调用通道要报出它实际发请求所用的凭证，而不是由编排层按供应商名称或配置重新读取。是否支持素材引用
按通道协议与模型声明：只有声明了的槽位才会收到 ``asset://`` 引用，其余槽位一律发送图片内容。

独立于各通道包：内置与自定义两种装配都要构造凭证引用，编排层读声明时也不必依赖具体通道。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

#: 参考生视频的参考图槽位；首帧、尾帧不在此列，见 :func:`provider_asset_roles`。
REFERENCE_IMAGE_ROLE = "reference_image"


@dataclass(frozen=True)
class InferenceCredentialRef:
    """调用通道发请求实际使用的推理凭证。

    ``base_url`` 是凭证或自定义供应商配置里的原值，``default_base_url`` 是原值为空时实际发往的默认地址，
    与素材库绑定确认时计算摘要的口径一致。``api_key`` 只在进程内用于核对绑定，不进日志与检查点。
    """

    credential_type: Literal["builtin", "custom"]
    credential_id: int
    api_key: str
    base_url: str | None
    default_base_url: str | None = None

    def __repr__(self) -> str:
        return (
            f"InferenceCredentialRef(credential_type={self.credential_type!r}, "
            f"credential_id={self.credential_id!r}, base_url={self.base_url!r})"
        )


def inference_credential(backend: object) -> InferenceCredentialRef | None:
    """调用通道报出的推理凭证；通道没有报出（不走素材库的协议、测试替身）时返回 None。"""
    value = getattr(backend, "inference_credential", None)
    return value if isinstance(value, InferenceCredentialRef) else None


def provider_asset_roles(backend: object) -> frozenset[str]:
    """调用通道对当前模型声明的、可以用素材引用代替图片内容的槽位；未声明时为空集。"""
    value = getattr(backend, "provider_asset_roles", None)
    return value if isinstance(value, frozenset) else frozenset()


#: 方舟「输入图片未通过审核」类错误码的前缀，如 ``InputImageSensitiveContentDetected.PrivacyInformation``。
_INPUT_IMAGE_REJECTION_PREFIX = "InputImageSensitiveContentDetected"


def is_input_image_rejection(provider_code: object) -> bool:
    return isinstance(provider_code, str) and provider_code.startswith(_INPUT_IMAGE_REJECTION_PREFIX)


class ProviderInputImageRejectedError(RuntimeError):
    """供应商以输入图片未通过审核拒绝了请求。

    ``reference_index`` 是被拒图片在请求参考图里的下标（0-based），只有能可靠确定时才有值；
    ``sent_as_asset`` 表示那张图片是以素材引用发出的。编排层据此点名具体的资产图或衍生图，
    并在被拒的是普通图片时提示用户上传到供应商素材库；不自动上传，也不重试生成。
    """

    def __init__(
        self,
        *,
        provider_code: str,
        provider_reason: str | None,
        reference_index: int | None,
        sent_as_asset: bool,
    ) -> None:
        self.provider_code = provider_code
        self.provider_reason = provider_reason
        self.reference_index = reference_index
        self.sent_as_asset = sent_as_asset
        self.code = "provider_input_image_rejected"
        self.params: dict[str, Any] = {"error_code": provider_code}
        if provider_reason:
            self.params["provider_reason"] = provider_reason
        super().__init__(f"{provider_code}: {provider_reason or ''}".strip())

    def name_reference(self, *, index: int, name: str) -> ProviderInputImageRejectedError:
        """点名被拒的参考图：``index`` 是提示词里的图片编号（1-based），``name`` 是资产或衍生名称。"""
        named = ProviderInputImageRejectedError(
            provider_code=self.provider_code,
            provider_reason=self.provider_reason,
            reference_index=self.reference_index,
            sent_as_asset=self.sent_as_asset,
        )
        named.code = "provider_input_asset_rejected_at" if self.sent_as_asset else "provider_input_image_rejected_at"
        named.params = {**self.params, "index": index, "name": name}
        return named


__all__ = [
    "REFERENCE_IMAGE_ROLE",
    "InferenceCredentialRef",
    "ProviderInputImageRejectedError",
    "inference_credential",
    "is_input_image_rejection",
    "provider_asset_roles",
]
