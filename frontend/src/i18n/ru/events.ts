import type enEvents from '../en/events';

export default {
  "label.grid": 'Многопанельная раскадровка «{{id}}»',
  "label.grid_split": 'Разделение многопанельной раскадровки «{{id}}»',
  "label.voice_sample": 'Прослушивание голоса «{{id}}»',
  "label.asset_image_character": 'Изображение материала для персонажа «{{id}}»',
  "label.asset_image_scene": 'Изображение материала для сцены «{{id}}»',
  "label.asset_image_prop": 'Изображение материала для реквизита «{{id}}»',
  "label.asset_image_product": 'Изображение материала для товара «{{id}}»',
  "label.asset_image_character_derivative": 'Изображение материала для производного варианта персонажа «{{id}}»',
  "label.skeleton_segments": 'Сегмент «{{id}}»',
  "label.skeleton_scenes": 'Сцена «{{id}}»',
  "label.skeleton_shots": 'Кадр «{{id}}»',
  "label.skeleton_video_units": 'Видеофрагмент «{{id}}»',
  "label.narration_audio": 'Закадровая озвучка «{{id}}»',
  "label.named_entity_character": 'Персонаж «{{id}}»',
  "label.named_entity_scene": 'Сцена «{{id}}»',
  "label.named_entity_prop": 'Реквизит «{{id}}»',
  "label.named_entity_product": 'Товар «{{id}}»',
  "label.named_entity_character_derivative": 'Производный вариант персонажа «{{id}}»',
  "label.character_reference_audio": 'Образец голоса персонажа «{{id}}»',
  "label.project_settings": 'Настройки проекта',
  "label.overview": 'Обзор проекта',
  "label.episode": '{{episode_name}}',
  "label.script_plan": 'План сценария серии «{{episode_name}}»',

  "entity.project": 'проекты',
  "entity.character": 'персонажи',
  "entity.scene": 'сцены',
  "entity.prop": 'реквизит',
  "entity.product": 'товары',
  "entity.segment": 'сегменты',
  "entity.drama_scene": 'сцены',
  "entity.shot": 'кадры',
  "entity.reference_unit": 'видеофрагменты',
  "entity.episode": 'серии',
  "entity.script_plan": 'планы сценария',
  "entity.overview": 'обзоры проекта',
  "entity.draft": 'результаты плана сценария',
  "entity.grid": 'многопанельные раскадровки',
  "entity.task": 'задачи',
  "entity.fallback": 'элементы',

  "noun.storyboard_image": 'изображения раскадровки',
  "noun.video": 'видео',
  "noun.grid": 'многопанельные раскадровки',
  "noun.narration_audio": 'закадровые озвучки',
  "noun.voice_sample": 'образцы голоса',

  "single.storyboard_ready": 'Изображение раскадровки готово: {{label}}',
  "single.video_ready": 'Видео готово: {{label}}',
  "single.generated": 'Готово: {{label}}',
  "single.completed": 'Завершено: {{label}}',
  "single.created": 'Создано: {{label}}',
  "single.deleted": 'Удалено: {{label}}',
  "single.updated": 'Обновлено: {{label}}',

  "deferred.storyboard_ready":
    'ИИ только что сгенерировал изображение раскадровки: {{label}}. Нажмите, чтобы посмотреть',
  "deferred.video_ready":
    'ИИ только что сгенерировал видео: {{label}}. Нажмите, чтобы посмотреть',
  "deferred.generated": 'Готово: {{label}}',
  "deferred.completed": 'Завершено: {{label}}',
  "deferred.created": 'ИИ только что добавил: {{label}}. Нажмите, чтобы посмотреть',
  "deferred.deleted": 'ИИ только что удалил: {{label}}. Нажмите, чтобы посмотреть',
  "deferred.updated": 'ИИ только что обновил: {{label}}. Нажмите, чтобы посмотреть',

  "group.generated": 'Сгенерировано: {{entity}} ({{count}}) — {{summary}}',
  "group.created": 'Добавлено: {{entity}} ({{count}}) — {{summary}}',
  "group.deleted": 'Удалено: {{entity}} ({{count}}) — {{summary}}',
  "group.updated": 'Обновлено: {{entity}} ({{count}}) — {{summary}}',

  "group_deferred.generated":
    'ИИ только что сгенерировал: {{entity}} ({{count}}) — {{summary}}. Нажмите, чтобы посмотреть',
  "group_deferred.created":
    'ИИ только что добавил: {{entity}} ({{count}}) — {{summary}}. Нажмите, чтобы посмотреть',
  "group_deferred.deleted":
    'ИИ только что удалил: {{entity}} ({{count}}) — {{summary}}. Нажмите, чтобы посмотреть',
  "group_deferred.updated":
    'ИИ только что обновил: {{entity}} ({{count}}) — {{summary}}. Нажмите, чтобы посмотреть',

  "list.separator": ", ",
  "list.more_suffix": " и другие",
} satisfies Record<keyof typeof enEvents, string> & Record<string, string>;
