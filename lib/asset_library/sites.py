"""供应商素材库站点：控制面主机、签名所用的服务名与区域。

控制面凭证只发往这里登记的官方主机。主机由站点与区域在代码中决定，不从推理凭证的 base_url
或用户输入推断；官方文档只给出每个站点的一个素材库区域，未登记的区域一律拒绝。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

AssetLibrarySite = Literal["volcengine", "byteplus"]

ASSET_API_SERVICE = "ark"
ASSET_API_VERSION = "2024-01-01"
DEFAULT_PROJECT_NAME = "default"


@dataclass(frozen=True)
class SiteSpec:
    #: 区域 → 控制面主机
    hosts: dict[str, str]

    @property
    def regions(self) -> list[str]:
        return list(self.hosts)

    @property
    def default_region(self) -> str:
        return next(iter(self.hosts))


SITES: dict[str, SiteSpec] = {
    # https://docs.volcengine.com/docs/ark/list-assets-api
    "volcengine": SiteSpec(hosts={"cn-beijing": "ark.cn-beijing.volcengineapi.com"}),
    # https://docs.byteplus.com/en/docs/modelark/list-assets-api
    "byteplus": SiteSpec(hosts={"ap-southeast-1": "ark.ap-southeast-1.byteplusapi.com"}),
}


def control_plane_host(site: str, region: str) -> str | None:
    """站点与区域对应的控制面主机；未登记的组合返回 None。"""
    spec = SITES.get(site)
    if spec is None:
        return None
    return spec.hosts.get(region)
