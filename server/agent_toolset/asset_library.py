"""供应商素材库工具的声明：查询图片的素材库关联、浏览素材库、上传当前图片、选择已有素材、解除关联。

不声明供应商素材的远端改名与删除：它们由供应商控制台管理。素材库账号、对象存储与推理凭证绑定只在设置页配置，
工具不接触原始密钥（``docs/adr/0114``）。
"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.media_tools.provider_assets import (
    ListProviderLibraryAssetsRequest,
    ProviderAssetLinksRequest,
    ReleaseProviderAssetLinkRequest,
    SelectProviderLibraryAssetRequest,
    UploadToProviderAssetLibraryRequest,
    get_provider_asset_links,
    list_provider_library_assets,
    release_provider_asset_link,
    select_provider_library_asset,
    select_summary,
    upload_summary,
    upload_to_provider_asset_library,
)

_LINK_FIELDS = (
    "每条关联含 id、account_id、version、state（pending 等待上传、submitting 提交中、processing 供应商处理中、"
    "uncertain 提交结果待查回、active 可用、failed 失败）、usable、provider_asset_id、error_code、error_message"
    " 与 polling（false 且状态未定时后台已停止自动查询）。"
)

GET_PROVIDER_ASSET_LINKS = ToolDeclaration(
    name="get_provider_asset_links",
    description=(
        "查询一张图片当前选中版本在供应商素材库（火山方舟 / BytePlus ModelArk）里的关联与入库状态，"
        "与 Web 资产卡片「上传到供应商素材库」显示的是同一份记录。"
        "返回 content_version（当前版本，没有图片时为 null）、object_storage_configured（上传所需的对象存储是否已配置）、"
        "accounts（可用的素材库账号：id、name、site、region、project_name）与 links。"
        + _LINK_FIELDS
        + "关联属于具体内容版本：换图后新版本不继承旧版本的关联，切回旧版本时它仍有效的关联照常使用。"
        "生成时只用状态为 active 的关联；处理中或失败的关联会让参考生视频明确失败，不会悄悄改发普通图片。"
        "recheck=true 时请后台立即重新查询仍在处理中的入库任务，同样立即返回，不等待供应商。"
        "资源类型不支持返回 unsupported_resource_type。"
    ),
    request_model=ProviderAssetLinksRequest,
    migration=Exempt("只读版本记录与数据库里的关联；recheck 只改数据库里的下次查询时间，不写项目文件。"),
    domain_key="provider_asset_links",
    handler=get_provider_asset_links,
)

LIST_PROVIDER_LIBRARY_ASSETS = ToolDeclaration(
    name="list_provider_library_assets",
    description=(
        "按素材库账号的站点、区域与项目范围，分页列出供应商素材库里已有的素材，新的在前。"
        "每条含 id、name、asset_type（Image / Video / Audio）、status（active / processing / failed / unknown）、"
        "error_code 与 created_at；另返回 total、page、page_size。只有 asset_type 为 Image 且 status 为 active 的素材"
        "可以用 select_provider_library_asset 选择。结果不含供应商的签名下载地址。只读，不改变任何关联。"
        "账号不存在返回 asset_library_account_not_found；供应商拒绝或请求失败时返回控制面错误码，原样转述给用户。"
    ),
    request_model=ListProviderLibraryAssetsRequest,
    migration=Exempt("只读供应商素材库，不读写项目数据。"),
    domain_key="provider_library_assets",
    handler=list_provider_library_assets,
)

UPLOAD_TO_PROVIDER_ASSET_LIBRARY = ToolDeclaration(
    name="upload_to_provider_asset_library",
    description=(
        "把一张图片当前选中的版本上传到供应商素材库，即 Web 资产卡片的「上传到供应商素材库」。"
        "只在用户明确要求上传这张图片时调用，并传 user_requested=true。生成被供应商审核拒绝不构成上传要求："
        "此时把拒绝原因转述给用户，说明可以上传到素材库，由用户决定；不要自行上传，也不要自动重新生成。"
        "上传冻结调用时选中的版本，之后改选其他版本不影响这次入库。图片经用户自有的对象存储中转后提交入库，"
        "入库与审核由后台完成：本工具立即返回入库任务（state=pending），不等待结果。之后用 get_provider_asset_links "
        "查询状态，状态为 active 后再按用户意愿重新生成；不要反复轮询等待。"
        "没有图片版本返回 provider_asset_upload_no_image；未配置对象存储返回 provider_asset_upload_needs_object_storage；"
        "账号不存在返回 asset_library_account_not_found，这三种都请用户到设置或资产卡片处理。"
        "当前版本在该账号已有关联或入库任务返回 provider_asset_link_exists，用户确认后先 release_provider_asset_link 再上传。"
        "成功返回入库任务，字段同 get_provider_asset_links 的 links[]。"
    ),
    request_model=UploadToProviderAssetLibraryRequest,
    migration=BLOCKED,
    domain_key="provider_asset_link",
    handler=upload_to_provider_asset_library,
    summary=upload_summary,
)

SELECT_PROVIDER_LIBRARY_ASSET = ToolDeclaration(
    name="select_provider_library_asset",
    description=(
        "从供应商素材库选择一张已有图片用于资产图，即 Web 的「从供应商素材库选择」。只在用户明确选定这张素材时调用。"
        "先取回素材的实际图片内容，按手动上传成品保存为资产图的新版本并设为当前版本，再为这个新版本建立可用关联；"
        "旧版本保留在版本历史里。取不回内容、素材不是可用图片或保存失败时不建立关联，当前版本不变。"
        "素材不是图片返回 provider_asset_import_not_image；还不可用返回 provider_asset_import_not_active；"
        "取不回内容返回 provider_asset_import_download_failed；资产不存在返回对应的 *_not_found。"
        "成功返回 content_version（新版本的 id、version、digest）与 link。"
    ),
    request_model=SelectProviderLibraryAssetRequest,
    migration=BLOCKED,
    domain_key="provider_asset_selection",
    handler=select_provider_library_asset,
    summary=select_summary,
)

RELEASE_PROVIDER_ASSET_LINK = ToolDeclaration(
    name="release_provider_asset_link",
    description=(
        "解除一条供应商素材关联，即 Web 的「解除关联」。只解除这个图片版本与供应商素材的关系，"
        "不删除 ArcReel 里的图片，也不删除供应商素材库里的素材；仍在处理中的入库任务解除后不再生效。"
        "解除后该版本生成时改发普通图片，也可以重新上传。只在用户同意后调用。"
        "关联不存在返回 provider_asset_link_not_found；已解除的关联原样返回，selected=false。"
    ),
    request_model=ReleaseProviderAssetLinkRequest,
    migration=Exempt("只改数据库里的关联记录，不写项目文件；迁移失败时仍须能解除失效关联。"),
    domain_key="provider_asset_link",
    handler=release_provider_asset_link,
)

ASSET_LIBRARY_TOOLS = (
    GET_PROVIDER_ASSET_LINKS,
    LIST_PROVIDER_LIBRARY_ASSETS,
    UPLOAD_TO_PROVIDER_ASSET_LIBRARY,
    SELECT_PROVIDER_LIBRARY_ASSET,
    RELEASE_PROVIDER_ASSET_LINK,
)

__all__ = [
    "ASSET_LIBRARY_TOOLS",
    "GET_PROVIDER_ASSET_LINKS",
    "LIST_PROVIDER_LIBRARY_ASSETS",
    "RELEASE_PROVIDER_ASSET_LINK",
    "SELECT_PROVIDER_LIBRARY_ASSET",
    "UPLOAD_TO_PROVIDER_ASSET_LIBRARY",
]
