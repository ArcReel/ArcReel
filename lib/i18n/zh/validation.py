"""校验与归档诊断消息（中文）。

由 ``lib.project.data_validator`` / ``server.services.project.project_archive`` / ``lib.script.script_skeleton``
以 ``arcreel_market_core.validation_messages.ValidationMessage`` 的形式产出、在各消费边界渲染。
"""

MESSAGES = {
    # ---- 透传 ----
    "val_literal": "{text}",
    # ---- 通用字段形状 ----
    "val_missing_field": "缺少必填字段：{field}",
    "val_missing_field_at": "{prefix}: 缺少必填字段 {field}",
    "val_field_type_string": "字段类型错误：{field} 应为字符串",
    "val_field_type_bool": "字段类型错误：{field} 应为布尔值",
    "val_field_type_number": "字段类型错误：{field} 应为数字",
    "val_field_type_integer": "字段类型错误：{field} 应为整数",
    "val_field_out_of_range": "{field} 的值 {value} 超出范围，应在 {min} 到 {max} 之间",
    "val_field_must_be_string": "{field} 必须是字符串",
    "val_field_must_be_string_typed": "{field} 必须是字符串，当前为 {actual}",
    "val_field_must_be_array": "{field} 必须是数组",
    "val_field_must_be_nonempty_array": "{field} 必须是非空数组",
    "val_field_must_be_nonempty_string": "{field} 必须是非空字符串",
    "val_field_must_be_object": "{field} 必须是对象",
    "val_field_invalid": "{field} 不合法：{detail}",
    "val_ledger_source_file_not_relative": "source_file 必须是项目内相对 POSIX 路径",
    "val_ledger_source_file_escapes": "source_file 不能是绝对路径或包含 ..",
    "val_ledger_start_after_end": "start 不能大于 end",
    "val_field_bad_timestamp": "{field} 不是合法的 ISO8601 时间戳：{value}",
    "val_item_must_be_object": "{prefix}: 必须是对象",
    "val_item_format_object": "{prefix}: 数据格式错误，应为对象",
    # ---- 路径引用 ----
    "val_path_empty": "{field}: 路径不能为空",
    "val_path_traversal": "{field}: 引用路径越界：{path}",
    "val_path_outside_dir": "{field}: 引用路径必须位于 {dir}/ 目录下：{path}",
    "val_path_missing": "{field}: 引用的文件不存在：{path}",
    "val_path_must_be_relative": "{field} 必须是项目内相对路径：{path}",
    # ---- 项目级字段 ----
    "val_content_mode_invalid": "content_mode 值无效：'{value}'，必须是 {allowed}",
    "val_source_kind_invalid": "{prefix}: source_kind 值无效：'{value}'，必须是 {allowed}",
    "val_generation_mode_invalid": "generation_mode 值无效：'{value}'，必须是 {allowed}",
    "val_deprecated_clues": "project.json 含已废弃字段 clues，请等待自动迁移或手动重启服务",
    "val_deprecated_field_removable": "{field} 字段已废弃（改为读时计算），可安全移除",
    "val_cannot_load_project_json": "无法加载 project.json: {path}",
    "val_cannot_load_script": "无法加载脚本文件：{path}",
    "val_unrecognized_entry": "发现未识别的附加文件/目录：{name}",
    "val_novel_must_be_object": "novel 字段必须是对象",
    # ---- 剧集条目与账本 ----
    "val_ledger_status_type": "{prefix}: ledger_status 必须是字符串，当前取值：{value}",
    "val_source_origin_invalid": "{prefix}: source_origin 值无效：'{value}'，必须是 {allowed}",
    "val_source_range_requires_whole_source": "{prefix}: 只有切自整本源文（source_origin 为 whole_source）的集可以带 source_range",
    "val_whole_source_file_invalid": "whole_source_files[{index}] 必须是带 source_file 的对象，文件直接位于 source/ 下，扩展名为 .txt 或 .md",
    "val_episode_missing_num_at": "{prefix}: 缺少必填字段 episode (整数)",
    "val_episode_missing_title_at": "{prefix}: 缺少必填字段 title (字符串，可为空)",
    "val_episode_missing_num": "缺少必填字段：episode (整数)",
    # ---- 广告/短片项目 ----
    "val_ad_only_field": "{field} 仅广告/短片项目（content_mode=ad）可用",
    "val_ad_missing_target_duration": "缺少必填字段：target_duration（广告/短片项目的目标总时长，秒）",
    "val_ad_target_duration_invalid": "target_duration 值无效：{value}，必须为正整数秒",
    "val_ad_no_default_duration": "广告/短片项目不持有 default_duration（分镜时长按 target_duration 预算逐个分镜规划）",
    "val_ad_no_episode_target_duration": "广告/短片项目不持有 episode_target_duration（整集体量按 target_duration 预算规划）",
    "val_ad_no_grid_storyboard": "广告/短片项目不支持多宫格分镜（grid_storyboard）",
    "val_ad_episodes_single": "广告/短片项目 episodes 必须恒为第 1 集单条",
    "val_ad_shots_missing": "ad 脚本缺少 shots 数组",
    "val_ad_duration_drift": (
        "脚本总时长 {total} 秒与 target_duration {target} 秒偏差 {delta:.0%}，"
        "超过 {threshold:.0%} 观察阈值（仅提示，不阻塞保存）"
    ),
    # ---- 资产目录 ----
    "val_asset_format_object": "{asset_type} '{name}' 数据格式错误，应为对象",
    "val_asset_field_must_be_object": "{asset_type} '{name}'.{field} 必须是对象",
    "val_asset_field_must_be_string": "{asset_type} '{name}'.{field} 必须是字符串，当前为 {actual}",
    "val_asset_field_bad_timestamp": "{asset_type} '{name}'.{field} 不是合法的 ISO8601 时间戳：{value}",
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
        "{prefix}: 估算说话时长 {spoken:.1f} 秒超过分镜时长 {duration} 秒逾 {tolerance:.0%}"
        "（容差上界 {budget:.1f} 秒），长对白可能说不完或语速畸快（仅提示，不阻塞保存）"
    ),
    # ---- ad 分镜 ----
    "val_shot_duration_missing_zero": "{prefix}: 缺少 duration_seconds，将按 0 计入总时长",
    "val_shot_duration_out_of_range": (
        "{prefix}: duration_seconds 值无效 '{value}'，reference_video 路径必须是 {low}～{high} 之间的整数"
    ),
    "val_shot_missing_voiceover_text": "{prefix}: 缺少必填字段 voiceover_text（口播文案，可为空字符串）",
    # ---- 参考生视频单元 ----
    "val_unit_id_missing": "{prefix}: 缺少 unit_id",
    "val_unit_id_missing_required": "{prefix}: 缺少必填字段 unit_id",
    "val_unit_id_duplicate": "{prefix}: unit_id 重复 '{value}'",
    "val_video_units_missing": "reference_video 脚本缺少 video_units 数组",
    "val_unit_duration_range": "{prefix}: duration_seconds 必须是 {low}～{high} 之间的整数",
    # ---- 骨架与生成模式失配 ----
    "val_skeleton_noun_segments": "分镜",
    "val_skeleton_noun_scenes": "分镜",
    "val_skeleton_noun_shots": "分镜",
    "val_skeleton_noun_video_units": "视频单元",
    "val_route_reference_video": "参考生视频（reference_video）",
    "val_route_storyboard": "分镜图生视频（storyboard）",
    "val_skeleton_mismatch_reference_known": (
        "脚本骨架与项目生成模式不符：项目生成模式是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本是 {actual}（{actual_noun}）骨架。"
        "请调用 generate_script_plan 重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_reference_none": (
        "脚本骨架与项目生成模式不符：项目生成模式是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本没有任何骨架数组。"
        "请调用 generate_script_plan 重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "脚本骨架与项目生成模式不符：项目生成模式是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本是 {actual}（{actual_noun}）骨架。"
        "请重跑分集拆分（script_plan）重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "脚本骨架与项目生成模式不符：项目生成模式是{route}，要求 {expected}（{expected_noun}）骨架，"
        "当前脚本没有任何骨架数组。"
        "请重跑分集拆分（script_plan）重新拆分该集，再重新生成脚本。该脚本仍可查看、编辑与导出。"
    ),
    # ---- 参考生视频时长收编迁移 ----
    "val_unit_duration_clamped": "unit {unit_id} 时长 {target}s 超出 {low}～{high}s 合理区间，已裁剪为 {clamped}s",
    "val_unit_duration_slotted": ("unit {unit_id} 时长 {duration}s 不是模型档位（{durations}）成员，已取档为 {slot}s"),
    # ---- 归档修复与导入导出诊断 ----
    "arch_source_encoding_unconverted": "源文件编码无法识别，未转换为 UTF-8：source/{name}（分集规划无法读取该文件）",
    "arch_provider_asset_links_not_restored": "项目已导入，但 {count} 条供应商素材关联没能保存，生成时这些图片照常发送图片内容；需要时重新上传或从素材库选择",
    "arch_non_standard_entry_excluded": "非标准顶层目录/文件 '{entry}' 未包含在导出中",
    "arch_invalid_project_json": "无法解析 {file}: {path}",
    "arch_version_history_malformed": "{location}: 版本历史的结构不符合预期格式",
    "arch_version_snapshot_path_unmanaged": "{location}: 版本记录的快照路径不在该资源类型的版本目录内",
    "arch_script_file_repaired": "{location}: 自动修复为 {path}",
    "arch_missing_script_file_pending": "{location}: 脚本尚未生成：{path}",
    "arch_missing_script_file": "{location}: 引用的文件不存在：{path}",
    "arch_invalid_script_json": "无法解析脚本文件：{path}",
    "arch_deprecated_source_file_removed": "novel.source_file 字段已废弃，已移除",
    "arch_deprecated_field_removed": "{field} 字段已废弃（改为读时计算），已移除",
    "arch_deprecated_clue_field_removed": "{items_key}[{index}]: 废弃字段 {field} 已移除（请改用 scenes/props）",
    "arch_missing_field_filled": "{items_key}[{index}]: 补全缺失字段 {field}",
    "arch_unit_unresolved_mentions": (
        "video_units[{index}]: 正文引用了不存在于 project.json 的资产名：{names}；这些引用不会生成参考图"
    ),
    "arch_generated_assets_defaults": "{label}[{index}].generated_assets: 补全默认字段 {fields}",
    "arch_missing_generated_assets": "{label}[{index}]: 补全缺失字段 generated_assets",
    "arch_invalid_generated_assets": "{label}[{index}]: generated_assets 形态异常（{actual}），已重置为默认结构",
    "arch_placeholder_character_added": "自动补充缺失角色定义：{name}",
    "arch_placeholder_asset_added": "自动补充缺失{asset_type}定义：{name}",
    "arch_canonical_path_normalized": "{location}: 规范化为 {path}",
    "arch_current_asset_materialized": "{location}: 从 {source} 恢复当前文件 {target}",
    "arch_current_asset_restored_from_version": "{location}: 从 {source} 恢复当前文件 {target}",
    # ---- 归档导入异常 ----
    "arch_invalid_conflict_policy": "无效的冲突策略",
    "arch_conflict_policy_unsupported": "conflict_policy 仅支持 prompt、rename 或 overwrite，收到：{value}",
    "arch_import_validation_failed": "导入包校验失败",
    "arch_artifact_activation_failed": "导入项目的产物状态不一致：{reason}",
    "arch_project_migration_failed": "导入包里的项目数据无法升级到当前版本：{reason}",
    "arch_import_confirmation_required": "导入包升级时调整了部分内容，请确认后再导入",
    "arch_not_a_zip": "上传文件不是有效的 ZIP 归档",
    "arch_zip_encrypted_entry": "ZIP 包含加密条目，无法导入：{name}",
    "arch_zip_absolute_path_entry": "ZIP 包含绝对路径条目：{name}",
    "arch_zip_traversal_entry": "ZIP 包含路径穿越条目：{name}",
    "arch_zip_symlink_entry": "ZIP 包含符号链接条目：{name}",
    "arch_zip_unparsable_member": "无法解析 {label}: {path}",
    "arch_multiple_manifests": "ZIP 中包含多个 arcreel-export.json，无法确定项目根目录",
    "arch_manifest_missing_project_json": "官方导出包缺少 project.json",
    "arch_no_project_json": "ZIP 中未找到 project.json",
    "arch_multiple_project_json": "ZIP 中包含多个 project.json，无法确定项目根目录",
    "arch_extract_path_traversal": "解压路径越界：{path}",
    "arch_conflict_detected": "检测到项目编号冲突",
    "arch_project_name_conflict": "项目编号 '{name}' 已存在，请选择覆盖现有项目或自动重命名导入。",
    # ---- 自定义调用端点 · 定义校验 ----
    "val_ce_missing_field": "缺少必填字段：{field}",
    "val_ce_unknown_field": "不认识的字段：{field}",
    "val_ce_removed_field": "字段已移除：{field}——{reason}",
    "val_ce_invalid_type": "类型不符，应为 {expected}",
    "val_ce_invalid_enum_value": "取值不在允许范围内，可选：{allowed}",
    "val_ce_invalid_value": "取值不符合格式约定：{detail}",
    "val_ce_schema_violation": "不符合定义格式：{detail}",
    "val_ce_schema_minimum_constraint": "允许的最小值或数量：{limit}",
    "val_ce_schema_maximum_constraint": "允许的最大值或数量：{limit}",
    "val_ce_schema_format_constraint": "不符合要求的格式：{constraint}",
    "val_ce_schema_forbidden_constraint": "取值落入了禁止范围",
    "val_ce_schema_generic_constraint": "未满足定义约束（{keyword}）",
    "val_ce_removed_reason_request_query": "静态与动态 query 都写进 url 模板，凭证 query 归 auth.query",
    "val_ce_removed_reason_status_codes": "HTTP 码策略归运行时：2xx 成功、429 与 5xx 重试、其余失败",
    "val_ce_removed_reason_polling_policy": "轮询间隔与超时是运行时策略，不进定义",
    "val_ce_removed_reason_extract_source": "取值根一律是响应体，HTTP 状态码不走 JSONPath",
    "val_ce_removed_reason_extract_usage_keys": "用量改挂 poll.extract.usage",
    "val_ce_removed_reason_mime_types": "素材格式不做白名单，由供应商在提交时拒绝",
    "val_ce_removed_reason_comfyui_capabilities": "ComfyUI 端点的能力只从节点绑定推导，定义不存能力声明",
    "val_ce_malformed_placeholder": (
        "{fragment} 不是合法占位符：只支持裸变量（如 prompt、inputs.first_frame），"
        "没有过滤器、下标与表达式，开括号也必须闭合"
    ),
    "val_ce_undeclared_variable": "占位符 {name} 引用了未声明的变量",
    "val_ce_api_key_outside_auth": "api_key 只能出现在 auth 节：凭证不进请求体与 URL，分享出去的定义也不该带上它",
    "val_ce_auth_without_api_key": "auth 节非空却没有引用 api_key：无凭证接口请把这一节留空，否则让它写入凭证",
    "val_ce_auth_header_conflict": "{header} 与 auth.headers 同名（不区分大小写）：凭证 header 只能由 auth 节写入",
    "val_ce_header_name_duplicate": "{header} 与同表里的 {first} 只差大小写：HTTP 头名不区分大小写，两条会一起发出去",
    "val_ce_auth_query_conflict": "URL 自带的 query 参数 {param} 与 auth.query 同名：凭证 query 只能由 auth 节写入",
    "val_ce_auth_query_reserved": "auth.query 的 {param} 与取产物那一跳自带的参数同名：下载时凭证会被它顶掉，请换一个参数名",
    "val_ce_task_id_out_of_scope": "task_id 只在 poll 与 result 节可用",
    "val_ce_result_id_out_of_scope": "result_id 只在 result 节可用",
    "val_ce_result_id_without_extract": "引用了 result_id，但 poll.extract 没有声明 result_id",
    "val_ce_input_out_of_scope": "素材 {name} 只能在 submit 节引用：轮询与取件请求不携带素材",
    "val_ce_list_input_requires_each": "{name} 是列表型素材，只能经 $each 展开，不能直接内插",
    "val_ce_each_in_not_list_input": "$each.in 指向的 {name} 不是已声明的列表型素材",
    "val_ce_each_shape_invalid": "$each 要么写 item 铺成数组元素，要么同时写 key 与 value 铺成键值对，两种写法不能混用",
    "val_ce_each_position_mismatch": (
        "$each 的写法与所在位置不符：数组位置写 item 铺成元素，对象位置写 key 与 value 铺成键值对"
    ),
    "val_ce_each_alias_reserved": "{name} 是循环体内的保留变量，不能用作 $each 的元素别名",
    "val_ce_when_unknown_input": "$when 指向的 {name} 不是已声明的素材",
    "val_ce_input_not_referenced": "声明了素材却没有在 submit 里引用：既不会发给供应商，也不能据此声明能力",
    "val_ce_enum_map_variable_not_allowed": "{variable} 不支持枚举映射，可映射的变量：{allowed}",
    "val_ce_default_variable_not_allowed": "{variable} 不支持缺省值，可声明缺省值的变量：{allowed}",
    "val_ce_default_value_type_invalid": "{variable} 的缺省值须是 {expected} 类型",
    "val_ce_default_value_not_in_enum_map": "{variable} 的缺省值 {value} 不在 enum_maps 表内，可用的值：{allowed}",
    "val_ce_status_map_target_invalid": "状态档位 {target} 不在 {allowed} 之内，过期语义请映射到 failed",
    "val_ce_capability_declared_without_input": "声明了 {capability}，但 submit 没有引用任何 {source} 素材，能力会撒谎",
    "val_ce_capability_input_without_declaration": (
        "submit 引用了 {source} 素材，却没有声明 {capability}，素材会发出去而界面不开放该能力"
    ),
    "val_ce_capability_incoherent": "能力 {capability} 与同组声明矛盾，须满足：{requirement}",
    "val_ce_capability_not_declared": "须在 capabilities 中把至少一项能力声明为 true：{allowed}",
    "val_ce_media_type_field_not_allowed": "{name} 不适用于媒体类型为 {media_type} 的定义",
    "val_ce_artifact_extract_missing": "缺少产物提取路径，至少写一项：{keys}",
    "val_ce_jsonpath_not_a_string": "取值路径必须是字符串：{path_expression}",
    "val_ce_jsonpath_surrounding_whitespace": "取值路径首尾不得有空白：{path_expression}",
    "val_ce_jsonpath_missing_root": "取值路径必须以 $ 开头：{path_expression}",
    "val_ce_jsonpath_recursive_descent": "取值路径禁用递归下降（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_union": "取值路径禁用联合选择器（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_slice_step": "取值路径禁用切片步长（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_function_extension": "取值路径禁用函数扩展（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_filter_root_reference": "过滤器内不得引用根节点（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_filter_non_singular": "过滤器内只允许单值查询（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_regex_operator": "取值路径禁用正则匹配运算符（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_syntax": "取值路径语法错误（第 {position} 个字符）：{path_expression}",
    "val_ce_jsonpath_evaluation_failed": "取值路径求值失败：{path_expression}",
    "val_ce_template_render_failed": "请求模板渲染失败：{detail}",
    "val_ce_auth_literal_credential": "该值疑似直接填写了真实密钥：请改用 api_key 占位符引用凭证，否则导出或分享定义时会泄露该密钥",
    "val_ce_template_url_not_string": "URL 模板必须渲染为字符串",
    "val_ce_unknown_asset_encoding": "未知素材编码：{encoding}",
    "val_ce_enum_map_value_missing": "enum_maps.{name} 缺少 '{value}' 的映射",
    "val_ce_template_text_variable_null": "变量 {name} 为空，不能嵌入混合文本",
    "val_ce_each_value_not_list": "$each.in 指向的 {name} 在运行时不是列表",
    "val_ce_each_key_duplicate": "对象式 $each 铺出的键 {field} 与同一对象里的其他键重名，请求无法承载全部素材",
    # ---- ComfyUI 端点 · 节点绑定与导入分流 ----
    "val_ce_comfyui_binding_required": "语义键 {binding_key} 必须绑定到节点后才能保存：提示词决定画什么，产物决定取哪个文件",
    "val_ce_comfyui_binding_key_not_allowed": "{media_type} 端点没有 {binding_key} 这一项语义，可用的语义键：{allowed}",
    "val_ce_comfyui_node_not_found": "workflow 里没有节点 {node}：workflow 改过之后节点 id 会变，请重新导入并确认节点绑定",
    "val_ce_comfyui_input_not_found": "节点 {node} 没有名为 {input} 的输入",
    "val_ce_comfyui_input_is_link": "节点 {node} 的 {input} 接的是上游连线，填进去的值运行时会被上游覆盖：请绑到字面值字段",
    "val_ce_comfyui_target_collision": (
        "节点 {node} 的 {input} 已经是 {owner} 的写入落点，{binding_key} 不能再写同一个字段：后填的值会盖掉先填的"
    ),
    "val_ce_comfyui_class_type_mismatch": (
        "条目记的节点 {node} 是 {class_type}，workflow 里它现在是 {actual}：重匹配按类型认身份，"
        "对不上会把绑定搬到别的节点上。请重新导入并确认节点绑定"
    ),
    "val_ce_comfyui_consumer_not_fed": (
        "参考图节点 {node} 的图流并没有接到节点 {consumer} 的 {input} 入口：记错了消费者，"
        "张数变少时会按一个不相干的入口去改图"
    ),
    "val_ce_comfyui_fps_conflict": (
        "帧率有多个互相矛盾的来源（{values}）：时长换算成帧数只能按一个帧率，请只保留一处帧率来源"
    ),
    "val_ce_comfyui_ui_format_workflow": "这是 ComfyUI 的 UI 格式 workflow，提交不了：请在 ComfyUI 里改用「Export (API)」导出",
    # ---- ComfyUI 端点 · 节点绑定推断的信号说明与提示 ----
    "val_ce_infer_manual_binding": "已保存的节点绑定，沿用用户确认过的落点",
    "val_ce_infer_title_marker": "节点标题带 ARCREEL: 标记，按标记认定",
    "val_ce_infer_external_node_family": "{family} 的参数化节点，声明的参数名是 {parameter}",
    "val_ce_infer_sampler_port_trace": "从节点 {node} 的 {port} 条件端口反溯到这里",
    "val_ce_infer_alias_with_class_type": "{class_type} 是这项语义的常见承载节点",
    "val_ce_infer_alias_only": "字段名 {input} 是这项语义的常见叫法",
    "val_ce_infer_link_trace": "它的输出接到节点 {consumer} 的 {input} 入口",
    "val_ce_infer_class_type_tier": "{class_type} 在同类候选里更可信",
    "val_ce_infer_output_chain": "它在最终产物那条链路上",
    "val_ce_infer_title_polarity": "节点标题「{title}」指向这一极",
    "val_ce_infer_note_reference_consumer_unknown": (
        "参考图接进节点 {node}（{class_type}）的 {input} 入口，这种入口在张数变少时改不动图："
        "实际张数少于格子数时会重复填充最后一张"
    ),
    "val_ce_infer_note_batch_size_above_one": (
        "节点 {node}（{class_type}）的 {input} 是 {value}，一次提交会出多张图；ArcReel 不改这个值"
    ),
    "val_ce_infer_note_manual_only_node": "节点 {node} 是 {class_type}，它承载的 {binding_keys} 推断不出来，请手动绑定",
    "val_ce_infer_note_computed_source": (
        "{binding_key} 的候选落在节点 {node} 的 {input} 上，但它的值由上游计算节点算出、写不进去，请手动指定"
    ),
    "val_ce_infer_note_rematched": "{binding_key} 的条目已重匹配到新节点 {nodes}",
    "val_ce_infer_note_binding_lost": (
        "{binding_key} 的条目在新 workflow 里找不到落点（原节点 {nodes}），已重跑推断，请确认"
    ),
    "val_ce_infer_note_target_taken": (
        "{binding_key} 的候选落在节点 {node} 的 {input} 上，而 {others} 也要写这个字段；"
        "同一个字段只能写一项语义，已把它从 {binding_key} 的候选里去掉"
    ),
    "val_ce_poll_without_task_id": "轮询请求没有引用 task_id，请确认这是有意的",
    "val_ce_jsonpath_wildcard_order": (
        "{path_expression} 含通配：对象通配只取首个，键序在前端预览与后端执行之间可能不同"
    ),
    # ---- 市场源校验 ----
    "val_market_index_unreadable": "无法读取索引文件：{detail}",
    "val_market_unsupported_schema_version": "索引格式版本 {version} 高于本工具支持的 {supported}",
    "val_market_missing_field": "缺少必填字段：{field}",
    "val_market_invalid_type": "类型错误，应为 {expected}",
    "val_market_invalid_value": "取值不符合约束 {keyword}：{constraint}",
    "val_market_slug_invalid": "slug「{value}」不合规：只允许小写字母、数字与连字符，以字母或数字开头，最长 64 个字符",
    "val_market_slug_duplicate": "slug「{slug}」在本市场源内重复",
    "val_market_slug_directory_mismatch": "slug「{slug}」与定义所在目录名「{directory}」不一致",
    "val_market_path_not_relative": "「{value}」必须是市场源内的相对路径：不能以 / 或协议开头，不能含 ..，不能指向源外",
    "val_market_file_missing": "引用的文件不存在：{value}",
    "val_market_symlink_not_allowed": "「{value}」是符号链接：客户端经 GitHub raw 抓取时只能拿到链接目标路径文本，请改为实体文件或目录",
    "val_market_icon_format_invalid": "图标必须是可读取的 PNG、WebP 或 SVG，且扩展名与内容一致",
    "val_market_icon_too_large": "图标 {size} 字节，超过上限 {limit} 字节",
    "val_market_icon_not_square": "图标必须是正方形，当前 {width}×{height}",
    "val_market_icon_ambiguous": "条目目录里只能有一个图标文件，发现：{icons}",
    "val_market_definition_unreadable": "无法读取定义：{detail}",
    "val_market_definition_invalid": "定义未通过校验（{code}）：{detail}",
    "val_market_projection_mismatch": "索引字段 {field} 为「{index_value}」，与定义 meta 的「{definition_value}」不一致",
    "val_market_min_app_version_invalid": "min_app_version「{value}」不是 semver（x.y.z）",
    "val_market_detail_meta_not_object": "定义缺少 meta 对象",
    "val_market_cli_check_passed": "市场源校验通过：{directory}",
    "val_market_cli_check_failed": "市场源校验未通过，共 {count} 个问题",
    "val_market_cli_generate_written": "已写入 {path}（{count} 个条目）",
    "val_market_cli_generate_failed": "无法生成索引，共 {count} 个问题",
    # Draft diagnostics; Agent reports retain their original messages.
    "draft_violation_source_text_empty": "{label} 的原文为空。复制该视频单元所依据的原文片段",
    "draft_violation_source_text_not_verbatim": "{label} 的原文不是小说原文的逐字片段：{excerpt}。原样复制原文，不要转述",
    "draft_violation_fullwidth_braces": "{label} 使用了全角花括号：{excerpt}。台词与画外音须使用半角花括号",
    "draft_violation_malformed_mention": "{label} 的资产引用格式有误：{excerpt}。使用 `@[资产名]`，名称不能为空，方括号须闭合",
    "draft_violation_dialogue_line_syntax": "{label} 的台词格式有误：{excerpt}。说话人不能为空，台词须由半角花括号包裹，否则会被当作画面描述",
    "draft_violation_unclosed_brace": "{label} 的花括号未闭合：{excerpt}。补齐成对的半角花括号",
    "draft_violation_braces_in_description": "{label} 的画面描述使用了花括号：{excerpt}。花括号只用于台词与画外音，不得嵌套",
    "draft_violation_empty_text": "{label} 的正文为空。补充画面描述和所需台词",
    "draft_violation_blank_description": "{label} 缺少画面描述。只有台词与画外音时无法生成画面",
    "draft_violation_unregistered_asset": "{label} 的 {field} 引用了未登记的资产：{names}。使用已登记名称，或在 new_assets 中补充处理决定",
    "draft_violation_unregistered_speaker": "{label} 的说话人未登记为角色：{names}。说话人决定参考音频，须使用已登记角色",
    "draft_violation_unregistered_speaker_derivative": "{label} 的说话人引用了未登记的衍生：{names}。先登记衍生，或改用本体角色名",
    "draft_violation_refs_over_limit": "{label} 的参考图数 {count} 超过上限 {limit}。把次要角色融入背景描述，不使用 `@` 引用",
    "draft_violation_speakers_over_limit": "{label} 的说话人数 {count} 超过模型参考音频段数上限 {limit}。按说话人拆分镜头，使每个视频单元不超过上限",
    "draft_violation_dialogue_overload": "{label} 的台词约需 {spoken} 秒，超过时长 {duration} 秒（宽容 {tolerance} 后上限 {budget} 秒）。选择更长档位、拆分单元或精简台词",
    "draft_violation_dialogue_line_count_changed": "{label} 的台词条数从 {before} 条变为 {after} 条。提示词编写须逐字保留台词",
    "draft_violation_dialogue_rewritten": "{label} 的第 {index} 条台词被改写（{before} → {after}）。提示词编写须逐字保留台词",
    "draft_violation_duration_off_tier": "{label} 的时长 {duration} 秒不在生效档位 {tiers} 内。选择支持的时长；已确认时长出档时，调整引用或重新拆分剧本规划",
    "draft_violation_invalid_segment_id": "{label} 的 segment_id 须使用 E{episode}S## 格式，且集号匹配",
    "draft_violation_duplicate_segment_id": "segment_id 重复：{ids}。每个分镜的 ID 须在本集内唯一",
    "draft_violation_blank_novel_text": "{label} 的 novel_text 为空。每个分镜须携带逐字取自原文的旁白",
    "draft_violation_novel_text_coverage": "各分镜的 novel_text 未按序、逐字、完整覆盖原文。源文件：{source}。原样复制并按序拼接；草稿的源文范围有误时，先更新源文范围",
    "draft_violation_schema_invalid": "草稿的 {path} 不符合产出结构：{detail}。检查必填字段、类型和取值",
    # Draft schema reasons by pydantic error type, rendered into draft_violation_schema_invalid's {detail}.
    "draft_schema_missing": "缺少必填字段",
    "draft_schema_extra_forbidden": "不支持此字段",
    "draft_schema_literal_error": "须为以下取值之一：{expected}",
    "draft_schema_less_than_equal": "不能大于 {le}",
    "draft_schema_greater_than_equal": "不能小于 {ge}",
    "draft_schema_less_than": "须小于 {lt}",
    "draft_schema_greater_than": "须大于 {gt}",
    "draft_schema_string_too_short": "至少需要 {min_length} 个字符",
    "draft_schema_string_too_long": "最多 {max_length} 个字符",
    "draft_schema_too_short": "至少需要 {min_length} 项",
    "draft_schema_too_long": "最多 {max_length} 项",
    "draft_schema_string_pattern_mismatch": "须符合格式 {pattern}",
    "draft_schema_string_type": "须为字符串",
    "draft_schema_int_type": "须为整数",
    "draft_schema_int_parsing": "须为整数",
    "draft_schema_int_from_float": "须为整数",
    "draft_schema_bool_type": "须为布尔值",
    "draft_schema_bool_parsing": "须为布尔值",
    "draft_schema_list_type": "须为数组",
    "draft_schema_model_type": "须为对象",
    "draft_schema_dict_type": "须为对象",
    "draft_schema_non_empty_list": "须为非空数组",
    "draft_schema_prompt_authoring_unreadable": "无法按提示词编写结果的结构读取",
    "draft_schema_with_value": "{reason}（当前值：{value}）",
    "draft_schema_choice_separator": "、",
    "draft_violation_formal_revision_conflict": "正式内容在生成期间已变化。结果已保留为草稿，合并最新正式内容后再晋升",
    "draft_violation_draft_revision_conflict": "已有草稿待处置，或草稿在生成期间已变化。先晋升、丢弃或合并现有草稿，再重试",
    "draft_violation_unit_count_changed": "提示词编写产出 {actual} 个视频单元，应为 {expected} 个。此阶段不得合并、拆分、增加或删除单元",
    "draft_violation_formal_revision_missing": "草稿缺少生成时的正式剧本基线，无法安全晋升。重新生成草稿后重试",
    "draft_violation_new_asset_invalid": "{label} 的结构有误。检查数组、必填字段与处理决定",
    "draft_violation_new_asset_name_invalid": "{label} 的 {field} 名称无效：{name}。改用有效的资产名",
    "draft_violation_new_asset_name_taken": "{label} 的名称「{name}」与{asset_type}「{existing}」重名。改用其他名称",
    "draft_violation_new_asset_derivative_not_character": "{label} 不是角色，无法登记衍生。改用 register、merge 或 skip",
    "draft_violation_new_asset_merge_cycle": "{label} 的归并关系成环：{chain}。修正 target，或改用 register / skip",
    "draft_violation_new_asset_target_unresolved": "{label} 的归入目标「{target}」不是已登记或本集新增的同类资产（{asset_type}）。修正 target，或改用 register / skip",
    "draft_violation_new_asset_base_unresolved": "{label} 的本体「{target}」不是已登记或本集新增的角色。修正 target，或改用 register / skip",
    "draft_violation_mixed_speech": "{label}（{location}）：台词与画外音不能混用。拆分视频单元",
    "draft_violation_needs_replan": "{label}（{location}）：发声组合需要重新规划。拆分视频单元",
    "draft_violation_parse_failed": "{label}（{location}）：无法解析发声内容。修正输入格式",
    "draft_violation_empty_speaker": "{label}（{location}）：台词缺少说话人。指定角色",
}
