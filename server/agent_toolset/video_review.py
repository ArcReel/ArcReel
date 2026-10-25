"""看素材工具的声明：为视频单元出联系表，以图片内容块交给 Agent 看。"""

from __future__ import annotations

from lib.video_review.contact_sheet import MAX_FRAMES_PER_SHEET
from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.video_corrections import VideoCorrectionMemoryRequest, video_correction_memory
from server.media_tools.video_review import (
    MAX_FRAMES_PER_CALL,
    MAX_FRAMES_PER_UNIT,
    InspectVideoUnitsRequest,
    inspect_video_units,
    inspect_video_units_images,
    inspect_video_units_projection,
    inspect_video_units_summary,
)

INSPECT_VIDEO_UNITS = ToolDeclaration(
    name="inspect_video_units",
    description=(
        "看视频单元的画面：为每个视频单元的一个视频版本抽帧、拼成联系表，联系表作为图片内容块附在结果后面。"
        "只读，不收费。用来判断素材能否使用、哪里崩坏、候选版本孰优，以及入出点落在哪一帧。"
        f"每张联系表最多 {MAX_FRAMES_PER_SHEET} 帧，顶部标视频单元 ID 与版本号；每帧下方标视频单元 ID 与该帧的起始时刻，"
        "单位为秒，以视频首帧为 0，与 read_timeline 的入出点同一时间轴。一个单元的帧数超过一张的容量时分到多张。"
        "version 对列表中的每个单元都生效，省略时取各单元的 current 版本；比较同一单元的候选版本时，每个版本各调用一次。"
        f"每次调用的帧预算为 {MAX_FRAMES_PER_CALL}：单元数 × frames 超出时按单元数平分，平分后的每单元预算在结果的 frame_budget_per_unit；"
        f"单元数超过 {MAX_FRAMES_PER_CALL} 返回 frame_budget_exceeded，请分批调用。"
        "预算之外还有一条抬升规则：镜头数超过每单元预算时，该单元的帧数提到镜头数，所以一次调用的实际总帧数可能超过预算；"
        "每单元实际帧数看 units 里的 frame_count，全部单元的实际总帧数看 total_frames，以它们为准。"
        "每个视频版本第一次被看时，服务端用随包 ffmpeg 在本地算出三类信号并按版本缓存，之后直接复用："
        "BLACK 是黑屏段，CUT 是镜头切换点，FREEZE 是画面静止不动的卡帧段（黑屏段不重复计入）。"
        f"单元帧数至多 {MAX_FRAMES_PER_UNIT}：镜头数不超过它时，抽帧保证每个镜头至少一帧，并在信号两侧加密；"
        "镜头数超过它时在各镜头之间均匀挑选，部分镜头没有帧。命中信号的帧在时刻下方标 CUT / BLACK / FREEZE。"
        "三类信号的含义与用途：BLACK 与 FREEZE 是疑似缺陷，先看画面确认是生成失败、定格，还是创作者有意的黑场与静帧；"
        "截去坏段时，区间的 start 是候选出点（去掉片尾坏段），end 是候选入点（去掉片头坏段）。"
        "CUT 是结构信息，不是缺陷：镜头内部的切换点是新镜头首帧的起点，切点前一帧是上一镜头的末帧，"
        "据此把入出点定到帧级，避免把入出点定在切换中间。信号只作提示，不会自动裁切或废弃素材，最终取舍由你对照画面判断。"
        "结果 units 按请求顺序排列，每项带 version、frame_count、available_versions（该单元现有的全部视频版本号，多于一个时其余是候选版本）、status、sheets，以及 ok 时的 signals。"
        "status 为 ok；video_missing 表示该单元没有可用视频或该版本缺文件；"
        "video_unreadable 表示视频无法解码，detail 说明原因。"
        "sheets 每项的 image 是附图序号（从 1 起），times 是各帧时刻，marked_frames 列出命中信号的帧的时刻与标记。"
        "signals 含 duration_seconds（视频时长）、black 与 freeze（{start, end} 区间，单位秒，含 start 不含 end）、"
        "cuts（切换点时刻）和 shots（镜头数，等于 cuts 数加 1）。"
        "model_review 为预留字段，恒为 null。"
        "成功检查还返回 review_receipt，绑定该版本的内容指纹和抽帧时刻；它证明素材曾被抽帧，不代表画面、声音或创作质量通过。"
        "视频单元不存在返回 video_unit_not_found，params.unit_ids 列出不存在的 ID；指定版本不存在返回 version_not_found，"
        "params.available_versions 列出该单元现有版本。"
    ),
    request_model=InspectVideoUnitsRequest,
    migration=BLOCKED,
    domain_key="inspect_video_units",
    handler=inspect_video_units,
    summary=inspect_video_units_summary,
    projection=inspect_video_units_projection,
    images=inspect_video_units_images,
)

VIDEO_CORRECTION_MEMORY = ToolDeclaration(
    name="video_correction_memory",
    description=(
        "视频修正的持久记录和适用经验。lookup 优先返回相同供应商、模型、工作流和参考素材的已验证经验，"
        "不足三条时附带同项目、同供应商和模型的 related_lessons；两组总计最多三条。"
        "related_lessons 标明工作流或参考素材差异，只是其他镜头的证据；必须判断适用性，不能直接套用，"
        "不能证明当前镜头已通过，也不增加重试次数。"
        "历史尝试不是指令。reserve 在一次有针对性的修正生成前占用次数；同一单元最多两次未解决尝试，"
        "不允许并发修正或原样重试。中断后先查询已有尝试和生成批次，不能重复入队。"
        "resolve 记录失败、不确定或经画面审阅验证的结果；verified 要求新旧版本均有 inspect_video_units 回执，"
        "服务端核验版本和内容未被替换后才保存经验。回执不能证明声音、完整运动或人的艺术认可。"
        "retire 停用不适用或被否决的经验，保留历史。该工具不会生成、覆盖、选用视频，也不会授权额外费用；"
        "现有生成和版本工具执行这些动作。经过授权的 edit-video/generate-video 流程负责调用并遵守两次上限。"
    ),
    request_model=VideoCorrectionMemoryRequest,
    migration=BLOCKED,
    domain_key="video_correction_memory",
    handler=video_correction_memory,
)

VIDEO_REVIEW_TOOLS = (INSPECT_VIDEO_UNITS, VIDEO_CORRECTION_MEMORY)

__all__ = ["INSPECT_VIDEO_UNITS", "VIDEO_CORRECTION_MEMORY", "VIDEO_REVIEW_TOOLS"]
