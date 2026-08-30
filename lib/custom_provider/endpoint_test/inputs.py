"""端点测试三模式共用的输入值对象：调用参数、凭证、素材。

参数集与 ``VideoGenerationRequest`` 同名，唯独没有 seed——产品界面无处设置它，试跑里恒为 null
只会让模板把键删掉，留一个永远走不到的分支。素材不落资产库、不进项目：调用方读进内存交给这里，
用完即丢。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from lib.custom_provider.endpoint_definition import AssetData

#: 素材来源名（``inputs.*.source`` 的枚举）到「是否列表型」的对应，multipart 字段名即来源名。
ASSET_SOURCES: Mapping[str, bool] = {
    "start_image": False,
    "end_image": False,
    "reference_images": True,
    "reference_audio_files": True,
}


@dataclass(frozen=True)
class EndpointTestParameters:
    """一次端点测试的调用参数。"""

    model: str
    prompt: str
    duration_seconds: int = 5
    aspect_ratio: str = "9:16"
    resolution: str | None = None
    generate_audio: bool = True


@dataclass(frozen=True)
class EndpointTestCredentials:
    """内联凭证。按 ``provider_id`` 读库的入口在服务边界换成同一个值对象。"""

    base_url: str
    api_key: str


@dataclass(frozen=True)
class EndpointTestAssets:
    """按来源名归拢的素材内容。缺席的来源取 ``None``（列表型取空列表）。"""

    by_source: Mapping[str, AssetData | Sequence[AssetData] | None] = field(default_factory=dict)

    def get(self, source: str) -> AssetData | Sequence[AssetData] | None:
        return self.by_source.get(source)

    def items(self, source: str) -> list[AssetData]:
        """列表型来源的素材；缺席或单值来源取空列表。"""
        value = self.by_source.get(source)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            return list(value)
        return []

    def single(self, source: str) -> AssetData | None:
        """单值型来源的素材；缺席即 ``None``。"""
        value = self.by_source.get(source)
        return value if isinstance(value, AssetData) else None
