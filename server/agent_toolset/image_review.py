"""看图工具的声明：把资产图或分镜图以图片内容块交给 Agent 看。"""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.image_review import (
    MAX_IMAGE_EDGE,
    MAX_IMAGES_PER_CALL,
    InspectImagesRequest,
    inspect_images,
    inspect_images_images,
    inspect_images_projection,
    inspect_images_summary,
)

INSPECT_IMAGES = ToolDeclaration(
    name="inspect_images",
    description=(
        "看资产图或分镜图：按 ID 取角色、场景、道具、商品、角色衍生的资产图，或分镜的分镜图，"
        "以图片内容块附在结果后面。只读，不收费，不触发任何生成。"
        "用来判断生成结果能否使用：有没有崩坏，与设定或提示词是否相符，同一角色是否前后一致。"
        "一次调用只看同一类型（resource_type）；version 对列表中的每一项都生效，省略时取各项的 current 版本，"
        "比较同一项的历史版本时，每个版本各调用一次。"
        "默认 current 没有版本记录但登记了当前图时仍可看图，version 为 null、available_versions 为空；不补写版本记录。"
        f"每次调用最多 {MAX_IMAGES_PER_CALL} 张图，超出返回 image_limit_exceeded，请分批调用；"
        f"图片按原比例缩到长边不超过 {MAX_IMAGE_EDGE} px 的 JPEG。"
        "结果 images 按请求顺序排列，每项带 id、version、available_versions（该项现有的全部版本号）、status，"
        "ok 时带 image（附图序号，从 1 起），以及 prompt（生成该版本时登记的提示词，没有登记时省略）。"
        "status 为 ok；image_missing 表示该项还没有图或该版本缺文件；image_unreadable 表示图片无法读取或解码，detail 说明原因。"
        "ID 不存在返回 resource_not_found，params.ids 列出不存在的 ID；指定版本不存在返回 version_not_found，"
        "params.available_versions 列出该项现有版本。"
    ),
    request_model=InspectImagesRequest,
    migration=BLOCKED,
    domain_key="inspect_images",
    handler=inspect_images,
    summary=inspect_images_summary,
    projection=inspect_images_projection,
    images=inspect_images_images,
)

IMAGE_REVIEW_TOOLS = (INSPECT_IMAGES,)

__all__ = ["IMAGE_REVIEW_TOOLS", "INSPECT_IMAGES"]
