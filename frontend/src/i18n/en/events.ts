// Project change notification copy. `label.*` mirrors the stable `label_key`
// carried by each change event (see lib/i18n/*/events.py); the remaining keys
// build the sentence around it. Group sentences only render for counts above
// one, so `entity.*` and `noun.*` use plural wording.
export default {
  "label.grid": 'Multi-grid storyboard "{{id}}"',
  "label.grid_split": 'Multi-grid storyboard "{{id}}" split',
  "label.voice_sample": 'Voice preview for "{{id}}"',
  "label.asset_image_character": 'Asset image for character "{{id}}"',
  "label.asset_image_scene": 'Asset image for scene "{{id}}"',
  "label.asset_image_prop": 'Asset image for prop "{{id}}"',
  "label.asset_image_product": 'Asset image for product "{{id}}"',
  "label.skeleton_segments": 'Segment "{{id}}"',
  "label.skeleton_scenes": 'Scene "{{id}}"',
  "label.skeleton_shots": 'Shot "{{id}}"',
  "label.skeleton_video_units": 'Video unit "{{id}}"',
  "label.narration_audio": 'Narration audio "{{id}}"',
  "label.named_entity_character": 'Character "{{id}}"',
  "label.named_entity_scene": 'Scene "{{id}}"',
  "label.named_entity_prop": 'Prop "{{id}}"',
  "label.character_reference_audio": 'Reference audio for character "{{id}}"',
  "label.project_settings": "Project settings",
  "label.overview": "Project overview",
  "label.episode": "Episode {{episode}}",
  "label.draft_normalized_script": "Episode {{episode}} normalized script",
  "label.draft_segment_splitting": "Episode {{episode}} segment splitting",

  "entity.project": "projects",
  "entity.character": "characters",
  "entity.scene": "scenes",
  "entity.prop": "props",
  "entity.segment": "segments",
  "entity.drama_scene": "scenes",
  "entity.shot": "shots",
  "entity.reference_unit": "video units",
  "entity.episode": "episodes",
  "entity.overview": "project overviews",
  "entity.draft": "script plan results",
  "entity.grid": "multi-grid storyboards",
  // Task terminal states are refresh signals (important=false) and never reach
  // notification copy; this entry only keeps the mapping exhaustive.
  "entity.task": "tasks",
  "entity.fallback": "items",

  "noun.storyboard_image": "storyboard images",
  "noun.video": "videos",
  "noun.grid": "multi-grid storyboards",
  "noun.narration_audio": "narration audio",
  "noun.voice_sample": "voice previews",

  "single.storyboard_ready": "Storyboard image for {{label}} is ready",
  "single.video_ready": "Video for {{label}} is ready",
  "single.generated": "{{label}} is ready",
  "single.completed": "{{label}} is done",
  "single.created": "{{label}} created",
  "single.deleted": "{{label}} deleted",
  "single.updated": "{{label}} updated",

  "deferred.storyboard_ready":
    "AI just generated the storyboard image for {{label}}. Tap to view",
  "deferred.video_ready":
    "AI just generated the video for {{label}}. Tap to view",
  "deferred.generated": "{{label}} is ready",
  "deferred.completed": "{{label}} is done",
  "deferred.created": "AI just added {{label}}. Tap to view",
  "deferred.deleted": "AI just deleted {{label}}. Tap to view",
  "deferred.updated": "AI just updated {{label}}. Tap to view",

  "group.generated": "Generated {{count}} {{entity}}: {{summary}}",
  "group.created": "Added {{count}} {{entity}}: {{summary}}",
  "group.deleted": "Deleted {{count}} {{entity}}: {{summary}}",
  "group.updated": "Updated {{count}} {{entity}}: {{summary}}",

  "group_deferred.generated":
    "AI just generated {{count}} {{entity}}: {{summary}}. Tap to view",
  "group_deferred.created":
    "AI just added {{count}} {{entity}}: {{summary}}. Tap to view",
  "group_deferred.deleted":
    "AI just deleted {{count}} {{entity}}: {{summary}}. Tap to view",
  "group_deferred.updated":
    "AI just updated {{count}} {{entity}}: {{summary}}. Tap to view",

  "list.separator": ", ",
  "list.more_suffix": " and more",
};
