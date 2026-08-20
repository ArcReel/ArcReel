import type enEvents from '../en/events';

export default {
  "label.grid": 'Phân cảnh đa lưới "{{id}}"',
  "label.grid_split": 'Tách phân cảnh đa lưới "{{id}}"',
  "label.voice_sample": 'Mẫu giọng thử của "{{id}}"',
  "label.asset_image_character": 'Ảnh tài sản của nhân vật "{{id}}"',
  "label.asset_image_scene": 'Ảnh tài sản của cảnh "{{id}}"',
  "label.asset_image_prop": 'Ảnh tài sản của đạo cụ "{{id}}"',
  "label.asset_image_product": 'Ảnh tài sản của sản phẩm "{{id}}"',
  "label.skeleton_segments": 'Phân cảnh "{{id}}"',
  "label.skeleton_scenes": 'Cảnh "{{id}}"',
  "label.skeleton_shots": 'Cú máy "{{id}}"',
  "label.skeleton_video_units": 'Đơn vị video "{{id}}"',
  "label.narration_audio": 'Lời dẫn "{{id}}"',
  "label.named_entity_character": 'Nhân vật "{{id}}"',
  "label.named_entity_scene": 'Cảnh "{{id}}"',
  "label.named_entity_prop": 'Đạo cụ "{{id}}"',
  "label.character_reference_audio": 'Âm thanh tham chiếu của nhân vật "{{id}}"',
  "label.project_settings": "Cài đặt dự án",
  "label.overview": "Tổng quan dự án",
  "label.episode": "Tập {{episode}}",
  "label.draft_normalized_script": "Kịch bản đã chuẩn hóa của tập {{episode}}",
  "label.draft_segment_splitting": "Chia đoạn của tập {{episode}}",

  "entity.project": "dự án",
  "entity.character": "nhân vật",
  "entity.scene": "cảnh",
  "entity.prop": "đạo cụ",
  "entity.segment": "phân cảnh",
  "entity.drama_scene": "cảnh",
  "entity.shot": "cú máy",
  "entity.reference_unit": "đơn vị video",
  "entity.episode": "tập",
  "entity.overview": "tổng quan dự án",
  "entity.draft": "kết quả tổ chức nội dung",
  "entity.grid": "phân cảnh đa lưới",
  "entity.task": "tác vụ",
  "entity.fallback": "mục",

  "noun.storyboard_image": "ảnh phân cảnh",
  "noun.video": "video",
  "noun.grid": "phân cảnh đa lưới",
  "noun.narration_audio": "lời dẫn",
  "noun.voice_sample": "mẫu giọng thử",

  "single.storyboard_ready": "Đã tạo xong ảnh phân cảnh của {{label}}",
  "single.video_ready": "Đã tạo xong video của {{label}}",
  "single.generated": "Đã tạo xong {{label}}",
  "single.completed": "Đã hoàn tất {{label}}",
  "single.created": "Đã tạo {{label}}",
  "single.deleted": "Đã xóa {{label}}",
  "single.updated": "Đã cập nhật {{label}}",

  "deferred.storyboard_ready":
    "AI vừa tạo ảnh phân cảnh của {{label}}. Nhấn để xem",
  "deferred.video_ready": "AI vừa tạo video của {{label}}. Nhấn để xem",
  "deferred.generated": "Đã tạo xong {{label}}",
  "deferred.completed": "Đã hoàn tất {{label}}",
  "deferred.created": "AI vừa thêm {{label}}. Nhấn để xem",
  "deferred.deleted": "AI vừa xóa {{label}}. Nhấn để xem",
  "deferred.updated": "AI vừa cập nhật {{label}}. Nhấn để xem",

  "group.generated": "Đã tạo {{count}} {{entity}}: {{summary}}",
  "group.created": "Đã thêm {{count}} {{entity}}: {{summary}}",
  "group.deleted": "Đã xóa {{count}} {{entity}}: {{summary}}",
  "group.updated": "Đã cập nhật {{count}} {{entity}}: {{summary}}",

  "group_deferred.generated":
    "AI vừa tạo {{count}} {{entity}}: {{summary}}. Nhấn để xem",
  "group_deferred.created":
    "AI vừa thêm {{count}} {{entity}}: {{summary}}. Nhấn để xem",
  "group_deferred.deleted":
    "AI vừa xóa {{count}} {{entity}}: {{summary}}. Nhấn để xem",
  "group_deferred.updated":
    "AI vừa cập nhật {{count}} {{entity}}: {{summary}}. Nhấn để xem",

  "list.separator": ", ",
  "list.more_suffix": " và nhiều mục khác",
} satisfies Record<keyof typeof enEvents, string>;
