"""改图级联要认的两张节点类型表：哪些输入是可选入口，哪些节点是两两合并。

参考图张数少于绑定条目数时要删掉多余的读图节点，而删掉一个节点就得管它的下游——下游节点少
了一个输入，要么它本来就允许缺这个输入（可选入口），要么它的职责只是把两路合成一路（两两合并
节点），除此之外只能连它一起删。两种形态的判据都是节点类型，不是图的形状：``WanImageToVideo``
的 ``start_image`` 缺了照跑，``ImageBatch`` 的 ``image1`` 缺了就没有第一路可拼——同样是「少一个
输入」，处置相反，只有类型说得清。

表里只有 ComfyUI 官方内置节点（核心 ``nodes.py`` 与 ``comfy_extras``）里确实如此的类型，且合并
表只收固定两个输入的类型——带 ``inputcount`` 的动态多路节点少一路仍剩若干路，摘成两两合并会把
其余几路一并丢掉。名录外的类型一律按「其他节点」走级联删除：宁可多删一层交由 ``output`` 那道闸门
拦下，也不要凭猜测把一个必需输入摘掉、让 ComfyUI 在提交时报 ``node_errors``。
"""

from __future__ import annotations

#: ``class_type`` → 该类型上允许缺席的输入名。删掉上游后把这些键从 ``inputs`` 里摘掉即可。
OPTIONAL_INPUTS: dict[str, frozenset[str]] = {
    "WanImageToVideo": frozenset({"start_image", "clip_vision_output"}),
    "WanFirstLastFrameToVideo": frozenset(
        {"start_image", "end_image", "clip_vision_start_image", "clip_vision_end_image"}
    ),
    "WanVaceToVideo": frozenset({"control_video", "control_masks", "reference_image"}),
    "WanCameraImageToVideo": frozenset({"start_image", "clip_vision_output", "camera_conditions"}),
    "ImageStitch": frozenset({"image2"}),
    "ReferenceLatent": frozenset({"latent"}),
}

#: ``class_type`` → 该类型的两个同型输入名。少了一路时节点本身没有存在意义，消费者改接剩下
#: 那一路，节点随之删除（bypass）。顺序无关：留下的是两个键里还连着东西的那个。
MERGE_INPUT_PAIRS: dict[str, tuple[str, str]] = {
    "ImageBatch": ("image1", "image2"),
    "LatentBatch": ("samples1", "samples2"),
    "ConditioningCombine": ("conditioning_1", "conditioning_2"),
}
