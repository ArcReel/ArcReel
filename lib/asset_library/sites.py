"""Provider-library sites: control-plane hosts, signing service names, and regions.

Send credentials only to registered official hosts. Code derives hosts from sites/regions,
never inference base_url or user-input guesses. Official docs give one library region per site;
reject every unregistered region.
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
    #: Region → control-plane host
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
    """Control-plane host for a site/region pair, or None when unregistered."""
    spec = SITES.get(site)
    if spec is None:
        return None
    return spec.hosts.get(region)
