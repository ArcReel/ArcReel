"""校验与归档诊断消息（中文）。

由 ``lib.data_validator`` / ``server.services.project_archive`` / ``lib.script_skeleton``
以 ``lib.validation_messages.ValidationMessage`` 的形式产出、在各消费边界渲染。
"""

MESSAGES = {
    # ---- 透传 ----
    "val_literal": "{text}",
    # ---- 通用字段形状 ----
    "val_missing_field": "缺少必填字段: {field}",
    "val_missing_field_at": "{prefix}: 缺少必填字段 {field}",
    "val_field_type_string": "字段类型错误: {field} 应为字符串",
    "val_field_type_bool": "字段类型错误: {field} 应为布尔值",
    "val_field_type_number": "字段类型错误: {field} 应为数字",
    "val_speech_rate_out_of_range": "{field} 的值 {value} 超出范围，应在 {min} 到 {max} 之间",
    "val_field_must_be_string": "{field} 必须是字符串",
    "val_field_must_be_string_typed": "{field} 必须是字符串，当前为 {actual}",
    "val_field_must_be_array": "{field} 必须是数组",
    "val_field_must_be_nonempty_array": "{field} 必须是非空数组",
    "val_field_must_be_nonempty_string": "{field} 必须是非空字符串",
    "val_field_must_be_object": "{field} 必须是对象",
    "val_field_invalid": "{field} 不合法: {detail}",
    "val_ledger_source_file_not_relative": "source_file 必须是项目内相对 POSIX 路径",
    "val_ledger_source_file_escapes": "source_file 不能是绝对路径或包含 ..",
    "val_ledger_start_after_end": "start 不能大于 end",
    "val_field_bad_timestamp": "{field} 不是合法的 ISO8601 时间戳: {value}",
    "val_array_empty": "{field} 数组为空",
    "val_item_must_be_object": "{prefix}: 必须是对象",
    "val_item_format_object": "{prefix}: 数据格式错误，应为对象",
    # ---- 路径引用 ----
    "val_path_empty": "{field}: 路径不能为空",
    "val_path_traversal": "{field}: 引用路径越界: {path}",
    "val_path_outside_dir": "{field}: 引用路径必须位于 {dir}/ 目录下: {path}",
    "val_path_missing": "{field}: 引用的文件不存在: {path}",
    "val_path_must_be_relative": "{field} 必须是项目内相对路径: {path}",
    # ---- 项目级字段 ----
    "val_content_mode_invalid": "content_mode 值无效: '{value}'，必须是 {allowed}",
    "val_source_kind_invalid": "source_kind 值无效: '{value}'，必须是 {allowed}",
    "val_generation_mode_invalid": "generation_mode 值无效: '{value}'，必须是 {allowed}",
    "val_deprecated_clues": "project.json 含已废弃字段 clues，请等待自动迁移或手动重启服务",
    "val_deprecated_field_removable": "{field} 字段已废弃（改为读时计算），可安全移除",
    "val_cannot_load_project_json": "无法加载 project.json: {path}",
    "val_cannot_load_script": "无法加载脚本文件: {path}",
    "val_unrecognized_entry": "发现未识别的附加文件/目录: {name}",
    "val_novel_must_be_object": "novel 字段必须是对象",
    # ---- 剧集条目与账本 ----
    "val_ledger_status_type": "{prefix}: ledger_status 必须是字符串，当前取值: {value}",
    "val_episode_missing_num_at": "{prefix}: 缺少必填字段 episode (整数)",
    "val_episode_missing_title_at": "{prefix}: 缺少必填字段 title (字符串，可为空)",
    "val_episode_missing_num": "缺少必填字段: episode (整数)",
    # ---- 广告/短片项目 ----
    "val_ad_only_field": "{field} 仅广告/短片项目（content_mode=ad）可用",
    "val_ad_missing_target_duration": "缺少必填字段: target_duration（广告/短片项目的目标总时长，秒）",
    "val_ad_target_duration_invalid": "target_duration 值无效: {value}，必须为正整数秒",
    "val_ad_no_default_duration": "广告/短片项目不持有 default_duration（镜头时长按 target_duration 预算逐镜头规划）",
    "val_ad_no_grid_storyboard": "广告/短片项目不支持多宫格分镜（grid_storyboard）",
    "val_ad_episodes_single": "广告/短片项目 episodes 必须恒为第 1 集单条",
    "val_ad_shots_missing": "ad 脚本缺少 shots 数组或为空",
    "val_ad_duration_drift": (
        "脚本总时长 {total} 秒与 target_duration {target} 秒偏差 {delta:.0%}，"
        "超过 {threshold:.0%} 观察阈值（仅提示，不阻塞保存）"
    ),
    # ---- 资产目录 ----
    "val_asset_format_object": "{asset_type} '{name}' 数据格式错误，应为对象",
    "val_asset_missing_description": "{asset_type} '{name}' 缺少必填字段: description（须为非空字符串）",
    "val_asset_field_must_be_string": "{asset_type} '{name}'.{field} 必须是字符串，当前为 {actual}",
    "val_asset_field_bad_timestamp": "{asset_type} '{name}'.{field} 不是合法的 ISO8601 时间戳: {value}",
    "val_asset_field_must_be_string_list": "{asset_type} '{name}'.{field} 必须是字符串列表，当前为 {actual}",
    "val_asset_field_item_must_be_string": "{asset_type} '{name}'.{field}[{index}] 必须是字符串，当前为 {actual}",
    "val_asset_name_duplicate": (
        "项目资产名称重复：{duplicate_type}「{duplicate_name}」与{first_type}「{first_name}」"
        "按 strip + Unicode NFC 判定同名"
    ),
    # ---- 条目级引用 ----
    "val_refs_unregistered": "{prefix}: {field} 引用了不存在于 project.json 的{asset_type}: {names}",
    "val_missing_defaults_empty_array": "{prefix}: 缺少 {field}，将使用默认空数组",
    # ---- 条目通用 ----
    "val_id_format": "{prefix}: {field} 格式错误 '{value}'，应为 E{{n}}S{{nn}}",
    "val_missing_duration_default": "{prefix}: 缺少 duration_seconds，将使用默认值 {default}",
    "val_duration_invalid": "{prefix}: duration_seconds 值无效 '{value}'，必须为正整数",
    # ---- drama utterances ----
    "val_utterance_must_be_object": "{prefix} 必须是对象",
    "val_utterance_kind_invalid": "{prefix} kind 必须是 dialogue 或 voiceover",
    "val_utterance_text_invalid": "{prefix} text 必须是非空字符串",
    "val_utterance_speaker_type": "{prefix} speaker 必须是字符串或 null",
    "val_utterance_dialogue_speaker": "{prefix} dialogue 必须带非空 speaker",
    "val_utterance_voiceover_speaker": "{prefix} voiceover 不得带 speaker",
    "val_scene_speech_overflow": (
        "{prefix}: 估算说话时长 {spoken:.1f} 秒超过场景时长 {duration} 秒逾 {tolerance:.0%}"
        "（容差上界 {budget:.1f} 秒），长对白可能说不完或语速畸快（仅提示，不阻塞保存）"
    ),
    # ---- ad 镜头 ----
    "val_shot_duration_missing_zero": "{prefix}: 缺少 duration_seconds，将按 0 计入总时长",
    "val_shot_duration_out_of_range": (
        "{prefix}: duration_seconds 值无效 '{value}'，reference_video 路径必须是 {low}-{high} 之间的整数"
    ),
    "val_shot_missing_voiceover_text": "{prefix}: 缺少必填字段 voiceover_text（口播文案，可为空字符串）",
    # ---- 参考生视频单元 ----
    "val_unit_id_missing": "{prefix}: 缺少 unit_id",
    "val_unit_id_missing_required": "{prefix}: 缺少必填字段 unit_id",
    "val_unit_id_duplicate": "{prefix}: unit_id 重复 '{value}'",
    "val_video_units_missing": "reference_video 脚本缺少 video_units 数组或为空",
    "val_unit_duration_range": "{prefix}: duration_seconds 必须是 {low}-{high} 之间的整数",
    "val_ref_type_invalid": "{prefix}: type 无效: {value}",
    "val_ref_name_invalid": "{prefix}: name 必须是非空字符串: {value}",
    # ---- 骨架与路线失配 ----
    "val_skeleton_noun_segments": "分镜",
    "val_skeleton_noun_scenes": "场景",
    "val_skeleton_noun_shots": "镜头",
    "val_skeleton_noun_video_units": "视频单元",
    "val_route_reference_video": "参考生视频（reference_video）",
    "val_route_storyboard": "分镜图生视频（storyboard）",
    "val_skeleton_mismatch_reference_known": (
        "脚本骨架与项目生成路线不符：项目路线是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本是 {actual}（{actual_noun}）骨架。"
        "请重跑 split-reference-video-units 重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_reference_none": (
        "脚本骨架与项目生成路线不符：项目路线是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本没有任何骨架数组。"
        "请重跑 split-reference-video-units 重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "脚本骨架与项目生成路线不符：项目路线是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本是 {actual}（{actual_noun}）骨架。"
        "请重跑分集拆分（step1）重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "脚本骨架与项目生成路线不符：项目路线是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本没有任何骨架数组。"
        "请重跑分集拆分（step1）重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    # ---- 参考生视频时长收编迁移 ----
    "val_unit_duration_clamped": "unit {unit_id} 时长 {target}s 超出 {low}-{high}s 合理区间，已裁剪为 {clamped}s",
    "val_unit_duration_slotted": ("unit {unit_id} 时长 {duration}s 不是模型档位（{durations}）成员，已取档为 {slot}s"),
    # ---- 归档修复与导入导出诊断 ----
    "arch_source_encoding_unconverted": "源文件编码无法识别，未转换为 UTF-8：source/{name}（分集规划无法读取该文件）",
    "arch_non_standard_entry_excluded": "非标准顶层目录/文件 '{entry}' 未包含在导出中",
    "arch_invalid_project_json": "无法解析 {file}: {path}",
    "arch_script_file_repaired": "{location}: 自动修复为 {path}",
    "arch_missing_script_file_pending": "{location}: 脚本尚未生成: {path}",
    "arch_missing_script_file": "{location}: 引用的文件不存在: {path}",
    "arch_invalid_script_json": "无法解析脚本文件: {path}",
    "arch_deprecated_source_file_removed": "novel.source_file 字段已废弃，已移除",
    "arch_deprecated_field_removed": "{field} 字段已废弃（改为读时计算），已移除",
    "arch_deprecated_clue_field_removed": "{items_key}[{index}]: 废弃字段 {field} 已移除（请改用 scenes/props）",
    "arch_missing_field_filled": "{items_key}[{index}]: 补全缺失字段 {field}",
    "arch_missing_asset_definition": (
        "{items_key}[{index}]: {field} 引用了不存在于 project.json 的{asset_type}: {names}"
    ),
    "arch_unit_unresolved_mentions": (
        "video_units[{index}]: 正文引用了不存在于 project.json 的资产名: {names}；这些引用不会生成参考图"
    ),
    "arch_generated_assets_defaults": "{label}[{index}].generated_assets: 补全默认字段 {fields}",
    "arch_missing_generated_assets": "{label}[{index}]: 补全缺失字段 generated_assets",
    "arch_invalid_generated_assets": "{label}[{index}]: generated_assets 形态异常（{actual}），已重置为默认结构",
    "arch_placeholder_character_added": "自动补充缺失角色定义: {name}",
    "arch_canonical_path_normalized": "{location}: 规范化为 {path}",
    "arch_current_asset_materialized": "{location}: 从 {source} 恢复当前文件 {target}",
    "arch_current_asset_restored_from_version": "{location}: 从 {source} 恢复当前文件 {target}",
    # ---- 归档导入异常 ----
    "arch_invalid_conflict_policy": "无效的冲突策略",
    "arch_conflict_policy_unsupported": "conflict_policy 仅支持 prompt、rename 或 overwrite，收到: {value}",
    "arch_import_validation_failed": "导入包校验失败",
    "arch_artifact_activation_failed": "导入项目的产物状态不一致",
    "arch_not_a_zip": "上传文件不是有效的 ZIP 归档",
    "arch_zip_encrypted_entry": "ZIP 包含加密条目，无法导入: {name}",
    "arch_zip_absolute_path_entry": "ZIP 包含绝对路径条目: {name}",
    "arch_zip_traversal_entry": "ZIP 包含路径穿越条目: {name}",
    "arch_zip_symlink_entry": "ZIP 包含符号链接条目: {name}",
    "arch_zip_unparsable_member": "无法解析 {label}: {path}",
    "arch_multiple_manifests": "ZIP 中包含多个 arcreel-export.json，无法确定项目根目录",
    "arch_manifest_missing_project_json": "官方导出包缺少 project.json",
    "arch_no_project_json": "ZIP 中未找到 project.json",
    "arch_multiple_project_json": "ZIP 中包含多个 project.json，无法确定项目根目录",
    "arch_extract_path_traversal": "解压路径越界: {path}",
    "arch_conflict_detected": "检测到项目编号冲突",
    "arch_project_name_conflict": "项目编号 '{name}' 已存在，请选择覆盖现有项目或自动重命名导入。",
}
