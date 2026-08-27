"""Thông báo kiểm tra dữ liệu và chẩn đoán gói lưu trữ (tiếng Việt)."""

MESSAGES = {
    # ---- passthrough ----
    "val_literal": "{text}",
    # ---- hình dạng trường chung ----
    "val_missing_field": "Thiếu trường bắt buộc: {field}",
    "val_missing_field_at": "{prefix}: thiếu trường bắt buộc {field}",
    "val_field_type_string": "Sai kiểu trường: {field} phải là chuỗi",
    "val_field_type_bool": "Sai kiểu trường: {field} phải là boolean",
    "val_field_type_number": "Sai kiểu trường: {field} phải là số",
    "val_speech_rate_out_of_range": "Giá trị {value} của {field} nằm ngoài phạm vi; phải từ {min} đến {max}",
    "val_field_must_be_string": "{field} phải là chuỗi",
    "val_field_must_be_string_typed": "{field} phải là chuỗi, hiện là {actual}",
    "val_field_must_be_array": "{field} phải là mảng",
    "val_field_must_be_nonempty_array": "{field} phải là mảng không rỗng",
    "val_field_must_be_nonempty_string": "{field} phải là chuỗi không rỗng",
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
    "val_ad_no_grid_storyboard": "Dự án quảng cáo/phim ngắn không hỗ trợ phân cảnh đa lưới (grid_storyboard)",
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
    "val_asset_name_duplicate": (
        "Trùng tên tài nguyên dự án: {duplicate_type} '{duplicate_name}' xung đột với "
        "{first_type} '{first_name}' sau khi strip + chuẩn hóa Unicode NFC"
    ),
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
        "{prefix}: duration_seconds không hợp lệ '{value}', chế độ reference_video yêu cầu số nguyên "
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
    # ---- khung xương và chế độ tạo video ----
    "val_skeleton_noun_segments": "phân cảnh",
    "val_skeleton_noun_scenes": "cảnh",
    "val_skeleton_noun_shots": "cảnh quay",
    "val_skeleton_noun_video_units": "đơn vị video",
    "val_route_reference_video": "sinh video từ ảnh tham chiếu (reference_video)",
    "val_route_storyboard": "sinh video từ storyboard (storyboard)",
    "val_skeleton_mismatch_reference_known": (
        "Khung xương kịch bản không khớp chế độ tạo video của dự án: chế độ là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản hiện dùng {actual} ({actual_noun}). "
        "Hãy gọi generate_step1 để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_reference_none": (
        "Khung xương kịch bản không khớp chế độ tạo video của dự án: chế độ là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản không có mảng khung xương nào. "
        "Hãy gọi generate_step1 để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "Khung xương kịch bản không khớp chế độ tạo video của dự án: chế độ là {route}, yêu cầu khung "
        "{expected} ({expected_noun}), nhưng kịch bản hiện dùng {actual} ({actual_noun}). "
        "Hãy chạy lại bước tách tập (step1) để tách lại tập này rồi sinh lại kịch bản. "
        "Kịch bản vẫn có thể xem, sửa và xuất."
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "Khung xương kịch bản không khớp chế độ tạo video của dự án: chế độ là {route}, yêu cầu khung "
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
    "arch_unit_unresolved_mentions": (
        "video_units[{index}]: nội dung tham chiếu tên tài sản không có trong project.json: {names}; "
        "chúng sẽ không tạo ảnh tham chiếu"
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
    "arch_artifact_activation_failed": "Trạng thái sản phẩm của dự án nhập không nhất quán",
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
    # ---- Kiểm tra định nghĩa điểm cuối tùy chỉnh ----
    "val_ce_missing_field": "Thiếu trường bắt buộc: {field}",
    "val_ce_unknown_field": "Trường không xác định: {field}",
    "val_ce_removed_field": "Trường đã bị loại khỏi định dạng: {field} - {reason}",
    "val_ce_invalid_type": "Sai kiểu dữ liệu, cần {expected}",
    "val_ce_invalid_enum_value": "Giá trị không được phép, các giá trị hợp lệ: {allowed}",
    "val_ce_invalid_value": "Giá trị không đúng định dạng: {detail}",
    "val_ce_schema_violation": "Không khớp định dạng định nghĩa: {detail}",
    "val_ce_removed_reason_request_query": (
        "tham số query tĩnh và động đều viết trong mẫu url, query chứa thông tin xác thực thuộc về auth.query"
    ),
    "val_ce_removed_reason_status_codes": (
        "chính sách mã HTTP thuộc về thời gian chạy: 2xx thành công, 429 và 5xx thử lại, còn lại là thất bại"
    ),
    "val_ce_removed_reason_polling_policy": (
        "chu kỳ và thời gian chờ khi hỏi trạng thái là chính sách thời gian chạy, không nằm trong định nghĩa"
    ),
    "val_ce_removed_reason_extract_source": (
        "việc trích xuất luôn bắt đầu từ thân phản hồi; mã trạng thái HTTP không đi qua JSONPath"
    ),
    "val_ce_removed_reason_extract_usage_keys": "mức sử dụng nay nằm trong poll.extract.usage",
    "val_ce_removed_reason_mime_types": (
        "định dạng tư liệu không có danh sách cho phép; nhà cung cấp sẽ từ chối định dạng không nhận"
    ),
    "val_ce_removed_reason_media_type": "giai đoạn này chỉ có một loại phương tiện là video",
    "val_ce_undeclared_variable": "Chỗ giữ {name} tham chiếu tới một biến chưa được khai báo",
    "val_ce_api_key_outside_auth": (
        "api_key chỉ được xuất hiện trong mục auth: thông tin xác thực không đi vào thân yêu cầu hay URL, "
        "và cũng không nằm trong định nghĩa bạn chia sẻ"
    ),
    "val_ce_auth_without_api_key": (
        "Mục auth không rỗng nhưng không tham chiếu api_key: hãy để trống nếu API không cần thông tin xác thực, "
        "nếu không hãy để mục này ghi thông tin xác thực"
    ),
    "val_ce_auth_header_conflict": (
        "{header} trùng tên với auth.headers (không phân biệt hoa thường): chỉ mục auth mới được ghi header xác thực"
    ),
    "val_ce_auth_query_conflict": (
        "URL đã mang tham số query {param} trùng với auth.query: chỉ mục auth mới được ghi query xác thực"
    ),
    "val_ce_task_id_out_of_scope": "task_id chỉ dùng được trong mục poll và result",
    "val_ce_result_id_out_of_scope": "result_id chỉ dùng được trong mục result",
    "val_ce_result_id_without_extract": "Đã tham chiếu result_id nhưng poll.extract không khai báo result_id",
    "val_ce_input_out_of_scope": (
        "Tư liệu {name} chỉ được tham chiếu trong mục submit: yêu cầu hỏi trạng thái và lấy kết quả không mang tư liệu"
    ),
    "val_ce_list_input_requires_each": "{name} là tư liệu dạng danh sách, phải khai triển bằng $each thay vì chèn thẳng",
    "val_ce_each_in_not_list_input": "$each.in trỏ tới {name}, không phải tư liệu dạng danh sách đã khai báo",
    "val_ce_each_shape_invalid": (
        "$each hoặc dùng item để trải thành phần tử mảng, hoặc dùng đồng thời key và value để trải thành cặp "
        "khóa – giá trị; không được trộn hai cách viết"
    ),
    "val_ce_when_unknown_input": "$when trỏ tới {name}, không phải tư liệu đã khai báo",
    "val_ce_input_not_referenced": (
        "Tư liệu được khai báo nhưng không được tham chiếu trong submit: nó không bao giờ được gửi đi "
        "và cũng không thể làm cơ sở cho một năng lực"
    ),
    "val_ce_enum_map_variable_not_allowed": "{variable} không hỗ trợ ánh xạ, các biến ánh xạ được: {allowed}",
    "val_ce_status_map_target_invalid": (
        "Trạng thái {target} nằm ngoài {allowed}; hãy ánh xạ ngữ nghĩa hết hạn về failed"
    ),
    "val_ce_capability_declared_without_input": (
        "Đã khai báo {capability} nhưng submit không tham chiếu tư liệu {source} nào, năng lực sẽ sai sự thật"
    ),
    "val_ce_capability_input_without_declaration": (
        "submit tham chiếu tư liệu {source} nhưng không khai báo {capability}, "
        "tư liệu vẫn được gửi đi trong khi giao diện không mở năng lực đó"
    ),
    "val_ce_jsonpath_not_a_string": "Đường dẫn trích xuất phải là chuỗi: {path_expression}",
    "val_ce_jsonpath_surrounding_whitespace": (
        "Đường dẫn trích xuất không được có khoảng trắng ở hai đầu: {path_expression}"
    ),
    "val_ce_jsonpath_missing_root": "Đường dẫn trích xuất phải bắt đầu bằng $: {path_expression}",
    "val_ce_jsonpath_recursive_descent": (
        "Đường dẫn trích xuất không cho phép duyệt đệ quy (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_union": (
        "Đường dẫn trích xuất không cho phép bộ chọn hợp (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_slice_step": (
        "Đường dẫn trích xuất không cho phép bước nhảy khi cắt lát (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_function_extension": (
        "Đường dẫn trích xuất không cho phép hàm mở rộng (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_root_reference": (
        "Bộ lọc không được tham chiếu nút gốc (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_non_singular": (
        "Bộ lọc chỉ được dùng truy vấn đơn trị (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_regex_operator": (
        "Đường dẫn trích xuất không cho phép toán tử khớp biểu thức chính quy (tại ký tự {position}): {path_expression}"
    ),
    "val_ce_jsonpath_syntax": "Lỗi cú pháp đường dẫn trích xuất (tại ký tự {position}): {path_expression}",
    "val_ce_poll_without_task_id": "Yêu cầu hỏi trạng thái không tham chiếu task_id; hãy xác nhận đây là chủ ý",
    "val_ce_jsonpath_wildcard_order": (
        "{path_expression} dùng ký tự đại diện: với đối tượng chỉ lấy thành viên đầu tiên, "
        "và thứ tự khóa có thể khác nhau giữa bản xem trước và phía máy chủ"
    ),
}
