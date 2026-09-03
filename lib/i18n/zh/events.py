"""项目变更事件的条目标签文案。

事件载荷携带稳定的 ``label_key`` 与 ``label_params``，渲染边界（前端界面 / 后端日志兜底）
按各自语言查表成文，key 与 ``frontend/src/i18n/*/events.ts`` 的 ``label.*`` 一一对应。
"""

MESSAGES = {
    "event_label_grid": "多宫格分镜「{id}」",
    "event_label_grid_split": "多宫格分镜「{id}」切分",
    "event_label_voice_sample": "「{id}」试听样本",
    "event_label_asset_image_character": "角色「{id}」资产图",
    "event_label_asset_image_scene": "场景「{id}」资产图",
    "event_label_asset_image_prop": "道具「{id}」资产图",
    "event_label_asset_image_product": "商品「{id}」资产图",
    "event_label_asset_image_character_derivative": "角色衍生「{id}」资产图",
    "event_label_skeleton_segments": "分镜「{id}」",
    "event_label_skeleton_scenes": "分镜「{id}」",
    "event_label_skeleton_shots": "分镜「{id}」",
    "event_label_skeleton_video_units": "视频单元「{id}」",
    "event_label_narration_audio": "旁白配音「{id}」",
    "event_label_named_entity_character": "角色「{id}」",
    "event_label_named_entity_scene": "场景「{id}」",
    "event_label_named_entity_prop": "道具「{id}」",
    "event_label_character_reference_audio": "角色「{id}」参考音频",
    "event_label_project_settings": "项目设置",
    "event_label_overview": "项目概览",
    "event_label_episode": "第 {episode} 集",
    "event_label_draft_normalized_script": "第 {episode} 集规范化脚本",
    "event_label_draft_segment_splitting": "第 {episode} 集分镜拆分",
}
