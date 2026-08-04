"""Thông báo kiểm tra dữ liệu và chẩn đoán gói lưu trữ (tiếng Việt)."""

MESSAGES = {
    # ---- passthrough ----
    "val_literal": "{text}",
    # ---- hình dạng trường chung ----
    "val_missing_field": "Thiếu trường bắt buộc: {field}",
    "val_missing_field_at": "{prefix}: thiếu trường bắt buộc {field}",
    "val_field_type_string": "Sai kiểu trường: {field} phải là chuỗi",
    "val_field_type_bool": "Sai kiểu trường: {field} phải là boolean",
    "val_field_must_be_string": "{field} phải là chuỗi",
    "val_field_must_be_string_typed": "{field} phải là chuỗi, hiện là {actual}",
    "val_field_must_be_array": "{field} phải là mảng",
    "val_field_must_be_nonempty_array": "{field} phải là mảng không rỗng",
    "val_field_must_be_object": "{field} phải là đối tượng",
    "val_field_invalid": "{field} không hợp lệ: {detail}",
    "val_ledger_source_file_not_relative": "source_file phải là đường dẫn POSIX tương đối trong dự án",
    "val_ledger_source_file_escapes": "source_file không được là đường dẫn tuyệt đối hoặc chứa ..",
    "val_ledger_start_after_end": "start không được lớn hơn end",
    "val_field_bad_timestamp": "{field} không phải dấu thời gian ISO8601 hợp lệ: {value}",
    "val_array_empty": "Mảng {field} rỗng",
    "val_item_must_be_object": "{prefix}: phải là đối tượng",
    "val_item_format_object": "{prefix}: dữ liệu sai định dạng, phải là đối tượng",
    # ---- tham chiếu đường dẫn ----
    "val_path_empty": "{field}: đường dẫn không được để trống",
    "val_path_traversal": "{field}: đường dẫn tham chiếu vượt ra ngoài dự án: {path}",
    "val_path_outside_dir": "{field}: đường dẫn tham chiếu phải nằm trong thư mục {dir}/: {path}",
    "val_path_missing": "{field}: tệp được tham chiếu không tồn tại: {path}",
    "val_path_must_be_relative": "{field} phải là đường dẫn tương đối trong dự án: {path}",
    # ---- trường cấp dự án ----
    "val_content_mode_invalid": "content_mode không hợp lệ: '{value}', phải thuộc {allowed}",
    "val_source_kind_invalid": "source_kind không hợp lệ: '{value}', phải thuộc {allowed}",
    "val_generation_mode_invalid": "generation_mode không hợp lệ: '{value}', phải thuộc {allowed}",
    "val_deprecated_clues": (
        "project.json chứa trường clues đã ngừng dùng; hãy chờ di trú tự động hoặc khởi động lại dịch vụ"
    ),
    "val_deprecated_field_removable": "{field} đã ngừng dùng (nay được tính khi đọc), có thể xóa an toàn",
    "val_cannot_load_project_json": "Không tải được project.json: {path}",
    "val_cannot_load_script": "Không tải được tệp kịch bản: {path}",
    "val_unrecognized_entry": "Phát hiện tệp/thư mục bổ sung không nhận diện được: {name}",
    "val_novel_must_be_object": "Trường novel phải là đối tượng",
    # ---- mục tập phim và sổ cái ----
    "val_ledger_status_type": "{prefix}: ledger_status phải là chuỗi, giá trị hiện tại: {value}",
    "val_episode_missing_num_at": "{prefix}: thiếu trường bắt buộc episode (số nguyên)",
    "val_episode_missing_title_at": "{prefix}: thiếu trường bắt buộc title (chuỗi, có thể rỗng)",
    "val_episode_missing_num": "Thiếu trường bắt buộc: episode (số nguyên)",
    # ---- dự án quảng cáo / phim ngắn ----
    "val_ad_only_field": "{field} chỉ dùng được cho dự án quảng cáo/phim ngắn (content_mode=ad)",
    "val_ad_missing_target_duration": (
        "Thiếu trường bắt buộc: target_duration (tổng thời lượng mục tiêu tính bằng giây cho dự án quảng cáo/phim ngắn)"
    ),
    "val_ad_target_duration_invalid": "target_duration không hợp lệ: {value}, phải là số giây nguyên dương",
    "val_ad_no_default_duration": (
        "Dự án quảng cáo/phim ngắn không có default_duration "
        "(thời lượng từng cảnh quay được hoạch định theo ngân sách target_duration)"
    ),
    "val_ad_no_grid_storyboard": "Dự án quảng cáo/phim ngắn không hỗ trợ storyboard dạng lưới (grid_storyboard)",
    "val_ad_episodes_single": "Dự án quảng cáo/phim ngắn phải luôn có đúng một mục tập (tập 1)",
    "val_ad_shots_missing": "Kịch bản ad thiếu mảng shots hoặc mảng rỗng",
    "val_ad_duration_drift": (
        "Tổng thời lượng kịch bản {total} giây lệch {delta:.0%} so với target_duration {target} giây, "
        "vượt ngưỡng quan sát {threshold:.0%} (chỉ là thông báo, không chặn lưu)"
    ),
    # ---- danh mục tài sản ----
    "val_asset_format_object": "{asset_type} '{name}' sai định dạng dữ liệu, phải là đối tượng",
    "val_asset_missing_description": (
        "{asset_type} '{name}' thiếu trường bắt buộc: description (phải là chuỗi không rỗng)"
    ),
    "val_asset_field_must_be_string": "{asset_type} '{name}'.{field} phải là chuỗi, hiện là {actual}",
    "val_asset_field_bad_timestamp": ("{asset_type} '{name}'.{field} không phải dấu thời gian ISO8601 hợp lệ: {value}"),
    "val_asset_field_must_be_string_list": ("{asset_type} '{name}'.{field} phải là danh sách chuỗi, hiện là {actual}"),
    "val_asset_field_item_must_be_string": "{asset_type} '{name}'.{field}[{index}] phải là chuỗi, hiện là {actual}",
    # ---- tham chiếu cấp mục ----
    "val_refs_unregistered": "{prefix}: {field} tham chiếu {asset_type} không có trong project.json: {names}",
    "val_missing_defaults_empty_array": "{prefix}: thiếu {field}, sẽ dùng mảng rỗng mặc định",
    # ---- kiểm tra mục chung ----
    "val_id_format": "{prefix}: {field} sai định dạng '{value}', phải là E{{n}}S{{nn}}",
    "val_missing_duration_default": "{prefix}: thiếu duration_seconds, sẽ dùng giá trị mặc định {default}",
    "val_duration_invalid": "{prefix}: duration_seconds không hợp lệ '{value}', phải là số nguyên dương",
    # ---- utterances của drama ----
    "val_utterance_must_be_object": "{prefix} phải là đối tượng",
    "val_utterance_kind_invalid": "{prefix} kind phải là dialogue hoặc voiceover",
    "val_utterance_text_invalid": "{prefix} text phải là chuỗi không rỗng",
    "val_utterance_speaker_type": "{prefix} speaker phải là chuỗi hoặc null",
    "val_utterance_dialogue_speaker": "{prefix} dialogue phải có speaker không rỗng",
    "val_utterance_voiceover_speaker": "{prefix} voiceover không được có speaker",
    "val_scene_speech_overflow": (
        "{prefix}: thời lượng thoại ước tính {spoken:.1f} giây vượt thời lượng cảnh {duration} giây quá "
        "{tolerance:.0%} (trần dung sai {budget:.1f} giây); thoại dài có thể không kịp nói hoặc nghe quá nhanh "
        "(chỉ là thông báo, không chặn lưu)"
    ),
    # ---- cảnh quay quảng cáo ----
    "val_shot_duration_missing_zero": "{prefix}: thiếu duration_seconds, sẽ tính 0 vào tổng thời lượng",
    "val_shot_duration_out_of_range": (
        "{prefix}: duration_seconds không hợp lệ '{value}', tuyến reference_video yêu cầu số nguyên "
        "trong khoảng {low}-{high}"
    ),
    "val_shot_missing_voiceover_text": (
        "{prefix}: thiếu trường bắt buộc voiceover_text (lời thuyết minh, có thể là chuỗi rỗng)"
    ),
    # ---- đơn vị video tham chiếu ----
    "val_unit_id_missing": "{prefix}: thiếu unit_id",
    "val_unit_id_missing_required": "{prefix}: thiếu trường bắt buộc unit_id",
    "val_unit_id_duplicate": "{prefix}: unit_id trùng lặp '{value}'",
    "val_video_units_missing": "Kịch bản reference_video thiếu mảng video_units hoặc mảng rỗng",
    "val_unit_duration_range": "{prefix}: duration_seconds phải là số nguyên trong khoảng {low}-{high}",
    "val_reference_entry_must_be_object": "{prefix}: mỗi mục reference phải là đối tượng",
    "val_reference_type_invalid": "{prefix}: reference.type không hợp lệ: {value}",
    "val_reference_name_invalid": "{prefix}: reference.name phải là chuỗi không rỗng: {value}",
    "val_reference_not_in_bucket": (
        "{prefix}: {asset_type} '{name}' được tham chiếu không nằm trong nhóm tương ứng của project.json"
    ),
    "val_ref_type_invalid": "{prefix}: type không hợp lệ: {value}",
    "val_ref_name_invalid": "{prefix}: name phải là chuỗi không rỗng: {value}",
    "val_ref_unregistered_regroup": ("{prefix}: {asset_type} “{name}” được tham chiếu chưa đăng ký; cần tạo lại nhóm"),
    "val_reference_units_dangling_shots": (
        "{prefix}: các cảnh quay được tham chiếu không tồn tại ({ids}); cần tạo lại nhóm"
    ),
    # ---- khung xương và tuyến sinh video ----
    "val_skeleton_noun_segments": "phân cảnh",
    "val_skeleton_noun_scenes": "cảnh",
    "val_skeleton_noun_shots": "cảnh quay",
    "val_skeleton_noun_video_units": "đơn vị video",
    "val_route_reference_video": "sinh video từ ảnh tham chiếu (reference_video)",
    "val_route_storyboard": "sinh video từ storyboard (storyboard)",
    "val_skeleton_mismatch_reference_known": (
        "Khung xương kịch bản không khớp tuyến sinh video của dự án: tuyến là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản hiện dùng {actual} ({actual_noun}). "
        "Hãy chạy lại split-reference-video-units để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_reference_none": (
        "Khung xương kịch bản không khớp tuyến sinh video của dự án: tuyến là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản không có mảng khung xương nào. "
        "Hãy chạy lại split-reference-video-units để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "Khung xương kịch bản không khớp tuyến sinh video của dự án: tuyến là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản hiện dùng {actual} ({actual_noun}). "
        "Hãy chạy lại bước tách tập (step1) để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "Khung xương kịch bản không khớp tuyến sinh video của dự án: tuyến là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản không có mảng khung xương nào. "
        "Hãy chạy lại bước tách tập (step1) để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    # ---- di trú gộp thời lượng đơn vị video tham chiếu ----
    "val_unit_duration_clamped": (
        "unit {unit_id} có thời lượng {target}s nằm ngoài khoảng hợp lý {low}-{high}s; đã cắt về {clamped}s"
    ),
    "val_unit_duration_slotted": (
        "unit {unit_id} có thời lượng {duration}s không thuộc các mức thời lượng của mô hình ({durations}); "
        "đã chọn mức {slot}s"
    ),
    # ---- chẩn đoán sửa chữa và nhập/xuất gói lưu trữ ----
    "arch_source_encoding_unconverted": (
        "Không nhận diện được bảng mã tệp nguồn nên chưa chuyển sang UTF-8: source/{name} "
        "(khâu hoạch định tập không đọc được tệp này)"
    ),
    "arch_non_standard_entry_excluded": "Thư mục/tệp cấp cao không chuẩn '{entry}' không được đưa vào bản xuất",
    "arch_invalid_project_json": "Không phân tích được {file}: {path}",
    "arch_script_file_repaired": "{location}: đã tự động sửa thành {path}",
    "arch_missing_script_file_pending": "{location}: kịch bản chưa được sinh: {path}",
    "arch_missing_script_file": "{location}: tệp được tham chiếu không tồn tại: {path}",
    "arch_invalid_script_json": "Không phân tích được tệp kịch bản: {path}",
    "arch_deprecated_source_file_removed": "Trường novel.source_file đã ngừng dùng và đã được xóa",
    "arch_deprecated_field_removed": "{field} đã ngừng dùng (nay được tính khi đọc) và đã được xóa",
    "arch_deprecated_clue_field_removed": (
        "{items_key}[{index}]: đã xóa trường ngừng dùng {field} (hãy dùng scenes/props)"
    ),
    "arch_missing_field_filled": "{items_key}[{index}]: đã bổ sung trường còn thiếu {field}",
    "arch_missing_asset_definition": (
        "{items_key}[{index}]: {field} tham chiếu {asset_type} không có trong project.json: {names}"
    ),
    "arch_unit_missing_asset_definition": (
        "video_units[{index}]: references tham chiếu {asset_type} không có trong project.json: {names}"
    ),
    "arch_generated_assets_defaults": "{label}[{index}].generated_assets: đã bổ sung các trường mặc định {fields}",
    "arch_missing_generated_assets": "{label}[{index}]: đã bổ sung trường còn thiếu generated_assets",
    "arch_invalid_generated_assets": (
        "{label}[{index}]: generated_assets sai hình dạng ({actual}), đã đặt lại về cấu trúc mặc định"
    ),
    "arch_placeholder_character_added": "Đã tự động bổ sung định nghĩa nhân vật còn thiếu: {name}",
    "arch_canonical_path_normalized": "{location}: đã chuẩn hóa thành {path}",
    "arch_current_asset_materialized": "{location}: đã khôi phục tệp hiện hành {target} từ {source}",
    "arch_current_asset_restored_from_version": "{location}: đã khôi phục tệp hiện hành {target} từ {source}",
    # ---- lỗi nhập gói lưu trữ ----
    "arch_invalid_conflict_policy": "Chính sách xử lý xung đột không hợp lệ",
    "arch_conflict_policy_unsupported": "conflict_policy chỉ hỗ trợ prompt, rename hoặc overwrite; nhận được: {value}",
    "arch_import_validation_failed": "Kiểm tra gói nhập thất bại",
    "arch_not_a_zip": "Tệp tải lên không phải gói ZIP hợp lệ",
    "arch_zip_encrypted_entry": "ZIP chứa mục đã mã hóa nên không thể nhập: {name}",
    "arch_zip_absolute_path_entry": "ZIP chứa mục có đường dẫn tuyệt đối: {name}",
    "arch_zip_traversal_entry": "ZIP chứa mục vượt cấp thư mục: {name}",
    "arch_zip_symlink_entry": "ZIP chứa mục là liên kết tượng trưng: {name}",
    "arch_zip_unparsable_member": "Không phân tích được {label}: {path}",
    "arch_multiple_manifests": "ZIP chứa nhiều tệp arcreel-export.json nên không xác định được thư mục gốc dự án",
    "arch_manifest_missing_project_json": "Gói xuất chính thức thiếu project.json",
    "arch_no_project_json": "Không tìm thấy project.json trong ZIP",
    "arch_multiple_project_json": "ZIP chứa nhiều tệp project.json nên không xác định được thư mục gốc dự án",
    "arch_extract_path_traversal": "Đường dẫn giải nén vượt ra ngoài thư mục đích: {path}",
    "arch_conflict_detected": "Phát hiện trùng mã dự án",
    "arch_project_name_conflict": (
        "Mã dự án '{name}' đã tồn tại. Hãy chọn ghi đè dự án hiện có hoặc nhập với tên mới."
    ),
}
