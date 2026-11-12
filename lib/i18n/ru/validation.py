"""Validation and archive diagnostic messages (Russian)."""

MESSAGES = {
    # ---- passthrough ----
    "val_literal": "{text}",
    # ---- generic field shape ----
    "val_missing_field": "Не указано обязательное поле: {field}",
    "val_missing_field_at": "{prefix}: не указано обязательное поле {field}",
    "val_field_type_string": "Ошибка типа поля: {field} должно быть строкой",
    "val_field_type_bool": "Ошибка типа поля: {field} должно быть булевым значением",
    "val_field_type_integer": "Ошибка типа поля: {field} должно быть целым числом",
    "val_field_type_number": "Ошибка типа поля: {field} должно быть числом",
    "val_field_out_of_range": "Значение {value} поля {field} вне диапазона; оно должно быть от {min} до {max}",
    "val_field_must_be_string": "{field} должно быть строкой",
    "val_field_must_be_string_typed": "{field} должно быть строкой, получено {actual}",
    "val_field_must_be_array": "{field} должно быть массивом",
    "val_field_must_be_nonempty_array": "{field} должно быть непустым массивом",
    "val_field_must_be_nonempty_string": "{field} должно быть непустой строкой",
    "val_field_must_be_object": "{field} должно быть объектом",
    "val_field_invalid": "{field} некорректно: {detail}",
    "val_ledger_source_file_not_relative": "source_file должно быть относительным POSIX-путём внутри проекта",
    "val_ledger_source_file_escapes": "source_file не должно быть абсолютным путём и не должно содержать ..",
    "val_ledger_start_after_end": "start не может быть больше end",
    "val_field_bad_timestamp": "{field} не является допустимой отметкой времени ISO8601: {value}",
    "val_item_must_be_object": "{prefix}: должно быть объектом",
    "val_item_format_object": "{prefix}: неверный формат данных, ожидается объект",
    # ---- path references ----
    "val_path_empty": "{field}: путь не может быть пустым",
    "val_path_traversal": "{field}: путь ссылки выходит за пределы проекта: {path}",
    "val_path_outside_dir": "{field}: путь ссылки должен находиться в {dir}/: {path}",
    "val_path_missing": "{field}: файл по ссылке не существует: {path}",
    "val_path_must_be_relative": "{field} должно быть относительным путём внутри проекта: {path}",
    # ---- project-level fields ----
    "val_content_mode_invalid": "Недопустимое значение content_mode: «{value}», должно быть одним из {allowed}",
    "val_source_kind_invalid": "{prefix}: недопустимое значение source_kind «{value}», должно быть одним из {allowed}",
    "val_generation_mode_invalid": "Недопустимое значение generation_mode: «{value}», должно быть одним из {allowed}",
    "val_deprecated_clues": (
        "project.json содержит устаревшее поле clues; дождитесь автоматической миграции или перезапустите сервис"
    ),
    "val_deprecated_field_removable": "Поле {field} устарело (теперь вычисляется при чтении) и может быть безопасно удалено",
    "val_cannot_load_project_json": "Не удалось загрузить project.json: {path}",
    "val_cannot_load_script": "Не удалось загрузить файл сценария: {path}",
    "val_unrecognized_entry": "Обнаружен нераспознанный дополнительный файл или каталог: {name}",
    "val_novel_must_be_object": "Поле novel должно быть объектом",
    # ---- episode entries and ledger ----
    "val_ledger_status_type": "{prefix}: ledger_status должно быть строкой, получено: {value}",
    "val_source_origin_invalid": "{prefix}: недопустимое значение source_origin «{value}», должно быть одним из {allowed}",
    "val_source_range_requires_whole_source": "{prefix}: source_range может быть только у серии, вырезанной из всего исходного текста (source_origin — whole_source)",
    "val_whole_source_file_invalid": "whole_source_files[{index}] должно быть объектом с полем source_file, указывающим на файл .txt или .md непосредственно в source/",
    "val_episode_missing_num_at": "{prefix}: не указано обязательное поле episode (целое число)",
    "val_episode_missing_title_at": "{prefix}: не указано обязательное поле title (строка, может быть пустой)",
    "val_episode_missing_num": "Не указано обязательное поле: episode (целое число)",
    # ---- ad / short-film projects ----
    "val_ad_only_field": "{field} доступно только для рекламных проектов и короткометражек (content_mode=ad)",
    "val_ad_missing_target_duration": (
        "Не указано обязательное поле: target_duration (целевая общая длительность в секундах для рекламных проектов и короткометражек)"
    ),
    "val_ad_target_duration_invalid": "Недопустимое значение target_duration: {value}, должно быть целым положительным числом секунд",
    "val_ad_no_default_duration": (
        "Рекламные проекты и короткометражки не содержат default_duration "
        "(длительность кадров планируется по каждому кадру относительно target_duration)"
    ),
    "val_ad_no_episode_target_duration": (
        "Рекламные проекты и короткометражки не содержат episode_target_duration "
        "(общая длительность серии планируется относительно target_duration)"
    ),
    "val_ad_no_grid_storyboard": "Рекламные проекты и короткометражки не поддерживают многопанельную раскадровку (grid_storyboard)",
    "val_ad_episodes_single": "В рекламных проектах и короткометражках всегда должна быть ровно одна запись серии (серия 1)",
    "val_ad_shots_missing": "В сценарии рекламы нет массива shots",
    "val_ad_duration_drift": (
        "Общая длительность сценария {total} с отличается от target_duration {target} с на {delta:.0%}, "
        "что превышает порог наблюдения {threshold:.0%} (только информационно, сохранение не блокируется)"
    ),
    # ---- asset catalogs ----
    "val_asset_format_object": "{asset_type} «{name}»: неверный формат данных, ожидается объект",
    "val_asset_field_must_be_object": "{asset_type} «{name}».{field} должно быть объектом",
    "val_asset_field_must_be_string": "{asset_type} «{name}».{field} должно быть строкой, получено {actual}",
    "val_asset_field_bad_timestamp": "{asset_type} «{name}».{field} не является допустимой отметкой времени ISO8601: {value}",
    "val_asset_field_must_be_string_list": "{asset_type} «{name}».{field} должно быть списком строк, получено {actual}",
    "val_asset_field_item_must_be_string": "{asset_type} «{name}».{field}[{index}] должно быть строкой, получено {actual}",
    "val_asset_name_duplicate": (
        "Повторяющееся имя материала проекта: {duplicate_type} «{duplicate_name}» конфликтует с "
        "{first_type} «{first_name}» после strip + нормализации Unicode NFC"
    ),
    # ---- item-level references ----
    "val_refs_unregistered": "{prefix}: {field} ссылается на отсутствующие в project.json материалы ({asset_type}): {names}",
    "val_missing_defaults_empty_array": "{prefix}: {field} отсутствует, используется пустой массив по умолчанию",
    # ---- generic item checks ----
    "val_id_format": "{prefix}: недопустимый формат {field} «{value}», ожидается E{{n}}S{{nn}}",
    "val_missing_duration_default": "{prefix}: duration_seconds отсутствует, используется значение по умолчанию {default}",
    "val_duration_invalid": "{prefix}: недопустимое значение duration_seconds «{value}», должно быть целым положительным числом",
    # ---- drama utterances ----
    "val_utterance_must_be_object": "{prefix} должно быть объектом",
    "val_utterance_kind_invalid": "{prefix}: kind должен быть dialogue или voiceover",
    "val_utterance_text_invalid": "{prefix}: text должен быть непустой строкой",
    "val_utterance_speaker_type": "{prefix}: speaker должен быть строкой или null",
    "val_utterance_dialogue_speaker": "{prefix}: у dialogue должен быть непустой speaker",
    "val_utterance_voiceover_speaker": "{prefix}: у voiceover не должно быть speaker",
    "val_scene_speech_overflow": (
        "{prefix}: расчётная длительность речи {spoken:.1f} с превышает длительность сцены {duration} с более чем на "
        "{tolerance:.0%} (предел допуска {budget:.1f} с); длинный диалог может не уместиться или звучать слишком быстро "
        "(только информационно, сохранение не блокируется)"
    ),
    # ---- ad shots ----
    "val_shot_duration_missing_zero": "{prefix}: duration_seconds отсутствует, в общую длительность засчитан 0",
    "val_shot_duration_out_of_range": (
        "{prefix}: недопустимое значение duration_seconds «{value}»; режим reference_video требует целого числа "
        "от {low} до {high}"
    ),
    "val_shot_missing_voiceover_text": (
        "{prefix}: не указано обязательное поле voiceover_text (текст закадровой озвучки, может быть пустой строкой)"
    ),
    # ---- reference-video units ----
    "val_unit_id_missing": "{prefix}: unit_id отсутствует",
    "val_unit_id_missing_required": "{prefix}: не указано обязательное поле unit_id",
    "val_unit_id_duplicate": "{prefix}: повторяющийся unit_id «{value}»",
    "val_video_units_missing": "В сценарии reference_video нет массива video_units",
    "val_unit_duration_range": "{prefix}: duration_seconds должно быть целым числом от {low} до {high}",
    # ---- skeleton / route mismatch ----
    "val_skeleton_noun_segments": "сегменты",
    "val_skeleton_noun_scenes": "сцены",
    "val_skeleton_noun_shots": "кадры",
    "val_skeleton_noun_video_units": "видеофрагменты",
    "val_route_reference_video": "Из референса в видео (reference_video)",
    "val_route_storyboard": "Из раскадровки в видео (storyboard)",
    "val_skeleton_mismatch_reference_known": (
        "Структура сценария не соответствует режиму генерации проекта: режим «{route}» требует структуры "
        "{expected} ({expected_noun}), а этот сценарий использует {actual} ({actual_noun}). "
        "Вызовите generate_script_plan, чтобы заново разбить эту серию, затем сгенерируйте сценарий заново. "
        "Сценарий по-прежнему можно просматривать, редактировать и экспортировать."
    ),
    "val_skeleton_mismatch_reference_none": (
        "Структура сценария не соответствует режиму генерации проекта: режим «{route}» требует структуры "
        "{expected} ({expected_noun}), а в этом сценарии нет ни одного массива такой структуры. "
        "Вызовите generate_script_plan, чтобы заново разбить эту серию, затем сгенерируйте сценарий заново. "
        "Сценарий по-прежнему можно просматривать, редактировать и экспортировать."
    ),
    "val_skeleton_mismatch_storyboard_known": (
        "Структура сценария не соответствует режиму генерации проекта: режим «{route}» требует структуры "
        "{expected} ({expected_noun}), а этот сценарий использует {actual} ({actual_noun}). "
        "Запустите разбиение на серии (script_plan) заново, затем сгенерируйте сценарий заново. "
        "Сценарий по-прежнему можно просматривать, редактировать и экспортировать."
    ),
    "val_skeleton_mismatch_storyboard_none": (
        "Структура сценария не соответствует режиму генерации проекта: режим «{route}» требует структуры "
        "{expected} ({expected_noun}), а в этом сценарии нет ни одного массива такой структуры. "
        "Запустите разбиение на серии (script_plan) заново, затем сгенерируйте сценарий заново. "
        "Сценарий по-прежнему можно просматривать, редактировать и экспортировать."
    ),
    # ---- reference-video duration consolidation migration ----
    "val_unit_duration_clamped": (
        "Видеофрагмент {unit_id}: длительность {target} с выходит за разумный диапазон {low}–{high} с; приведена к {clamped} с"
    ),
    "val_unit_duration_slotted": (
        "Видеофрагмент {unit_id}: длительность {duration} с не входит в варианты длительности модели ({durations}); "
        "округлена до {slot} с"
    ),
    # ---- archive repair and import/export diagnostics ----
    "arch_source_encoding_unconverted": (
        "Не удалось определить кодировку исходного файла, файл не преобразован в UTF-8: source/{name} "
        "(планирование серий не может прочитать этот файл)"
    ),
    "arch_non_standard_entry_excluded": "Нестандартный каталог или файл верхнего уровня «{entry}» не включён в экспорт",
    "arch_invalid_project_json": "Не удалось разобрать {file}: {path}",
    "arch_version_history_malformed": "{location}: история версий не соответствует ожидаемому формату",
    "arch_version_snapshot_path_unmanaged": "{location}: путь снимка в записи версии находится вне каталога версий своего типа ресурса",
    "arch_script_file_repaired": "{location}: автоматически исправлено на {path}",
    "arch_missing_script_file_pending": "{location}: сценарий ещё не сгенерирован: {path}",
    "arch_missing_script_file": "{location}: файл по ссылке не существует: {path}",
    "arch_invalid_script_json": "Не удалось разобрать файл сценария: {path}",
    "arch_deprecated_source_file_removed": "Поле novel.source_file устарело и было удалено",
    "arch_deprecated_field_removed": "Поле {field} устарело (теперь вычисляется при чтении) и было удалено",
    "arch_deprecated_clue_field_removed": (
        "{items_key}[{index}]: устаревшее поле {field} удалено (используйте scenes/props)"
    ),
    "arch_missing_field_filled": "{items_key}[{index}]: добавлено отсутствующее поле {field}",
    "arch_unit_unresolved_mentions": (
        "video_units[{index}]: в тексте упоминаются названия материалов, отсутствующие в project.json: {names}; "
        "референсы для них не будут созданы"
    ),
    "arch_generated_assets_defaults": "{label}[{index}].generated_assets: заполнены поля по умолчанию {fields}",
    "arch_missing_generated_assets": "{label}[{index}]: добавлено отсутствующее поле generated_assets",
    "arch_invalid_generated_assets": (
        "{label}[{index}]: generated_assets имеет неверный формат ({actual}) и сброшено к структуре по умолчанию"
    ),
    "arch_placeholder_character_added": "Автоматически добавлено отсутствующее определение персонажа: {name}",
    "arch_placeholder_asset_added": "Автоматически добавлено отсутствующее определение: {asset_type} «{name}»",
    "arch_canonical_path_normalized": "{location}: нормализовано к {path}",
    "arch_current_asset_materialized": "{location}: текущий файл {target} восстановлен из {source}",
    "arch_current_asset_restored_from_version": "{location}: текущий файл {target} восстановлен из {source}",
    # ---- archive import errors ----
    "arch_invalid_conflict_policy": "Недопустимая политика конфликтов",
    "arch_conflict_policy_unsupported": "conflict_policy поддерживает только prompt, rename или overwrite; получено: {value}",
    "arch_import_validation_failed": "Проверка пакета для импорта не пройдена",
    "arch_artifact_activation_failed": "Состояние материалов импортированного проекта несогласовано: {reason}",
    "arch_project_migration_failed": "Данные проекта в архиве нельзя обновить до текущей версии: {reason}",
    "arch_import_confirmation_required": "При обновлении архива изменилась часть содержимого. Подтвердите импорт",
    "arch_not_a_zip": "Загруженный файл не является допустимым ZIP-архивом",
    "arch_zip_encrypted_entry": "ZIP содержит зашифрованную запись, импорт невозможен: {name}",
    "arch_zip_absolute_path_entry": "ZIP содержит запись с абсолютным путём: {name}",
    "arch_zip_traversal_entry": "ZIP содержит запись с обходом пути: {name}",
    "arch_zip_symlink_entry": "ZIP содержит запись символической ссылки: {name}",
    "arch_zip_unparsable_member": "Не удалось разобрать {label}: {path}",
    "arch_multiple_manifests": (
        "ZIP содержит несколько файлов arcreel-export.json; не удаётся определить корень проекта"
    ),
    "arch_manifest_missing_project_json": "В официальном пакете экспорта отсутствует project.json",
    "arch_no_project_json": "В ZIP не найден project.json",
    "arch_multiple_project_json": "ZIP содержит несколько файлов project.json; не удаётся определить корень проекта",
    "arch_extract_path_traversal": "Путь распаковки выходит за пределы целевого каталога: {path}",
    "arch_conflict_detected": "Обнаружен конфликт ID проекта",
    "arch_project_name_conflict": (
        "ID проекта «{name}» уже существует. Выберите: перезаписать существующий проект или импортировать под новым именем."
    ),
    # ---- Custom endpoint definition validation ----
    "val_ce_missing_field": "Не указано обязательное поле: {field}",
    "val_ce_unknown_field": "Неизвестное поле: {field}",
    "val_ce_removed_field": "Поле удалено из формата: {field} — {reason}",
    "val_ce_invalid_type": "Неверный тип; ожидается {expected}",
    "val_ce_invalid_enum_value": "Значение здесь недопустимо; допустимые значения: {allowed}",
    "val_ce_invalid_value": "Значение не соответствует формату: {detail}",
    "val_ce_schema_violation": "Не соответствует формату определения: {detail}",
    "val_ce_schema_minimum_constraint": "Минимально допустимое значение или количество: {limit}",
    "val_ce_schema_maximum_constraint": "Максимально допустимое значение или количество: {limit}",
    "val_ce_schema_format_constraint": "Не соблюдён требуемый формат: {constraint}",
    "val_ce_schema_forbidden_constraint": "Значение попадает под запрещённое ограничение",
    "val_ce_schema_generic_constraint": "Не выполнено ограничение определения ({keyword})",
    "val_ce_removed_reason_request_query": (
        "статические и динамические параметры запроса указываются в шаблоне url, параметры запроса для учётных данных — в auth.query"
    ),
    "val_ce_removed_reason_status_codes": (
        "обработка HTTP-статусов — политика времени выполнения: 2xx — успех, 429 и 5xx — повтор, остальные — ошибка"
    ),
    "val_ce_removed_reason_polling_policy": "интервал опроса и тайм-аут — параметры выполнения, а не определения",
    "val_ce_removed_reason_extract_source": "извлечение всегда начинается с тела ответа; код HTTP — не путь",
    "val_ce_removed_reason_extract_usage_keys": "расход теперь задаётся в poll.extract.usage",
    "val_ce_removed_reason_mime_types": "форматы материалов не ограничены списком; провайдер отклоняет то, что не может принять",
    "val_ce_removed_reason_comfyui_capabilities": (
        "эндпоинт ComfyUI выводит возможности из привязок узлов, поэтому определение не хранит их объявление"
    ),
    "val_ce_malformed_placeholder": (
        "{fragment} — недопустимый плейсхолдер: поддерживаются только простые переменные "
        "(например, prompt или inputs.first_frame), без фильтров, индексов и выражений, "
        "и каждая открывающая скобка должна быть закрыта"
    ),
    "val_ce_undeclared_variable": "Плейсхолдер {name} ссылается на необъявленную переменную",
    "val_ce_api_key_outside_auth": (
        "api_key допускается только в разделе auth: учётные данные не должны попадать в тело запроса и URL, "
        "а также в определения, которыми вы делитесь"
    ),
    "val_ce_auth_without_api_key": (
        "Раздел auth не пуст, но не ссылается на api_key: для API без учётных данных оставьте его пустым, "
        "иначе он должен записывать учётные данные"
    ),
    "val_ce_auth_header_conflict": (
        "{header} совпадает с auth.headers (без учёта регистра): заголовки учётных данных может задавать только раздел auth"
    ),
    "val_ce_header_name_duplicate": (
        "{header} и {first} в одном словаре различаются только регистром: имена заголовков HTTP "
        "нечувствительны к регистру, поэтому отправятся оба"
    ),
    "val_ce_auth_query_conflict": (
        "URL уже содержит параметр запроса {param}, объявленный в auth.query: "
        "параметры запроса с учётными данными может задавать только раздел auth"
    ),
    "val_ce_auth_query_reserved": "Запись auth.query {param} совпадает с параметром, который уже используется при скачивании результата; при скачивании учётные данные будут перезаписаны, поэтому выберите другое имя",
    "val_ce_task_id_out_of_scope": "task_id доступен только в разделах poll и result",
    "val_ce_result_id_out_of_scope": "result_id доступен только в разделе result",
    "val_ce_result_id_without_extract": "Есть ссылка на result_id, но poll.extract не объявляет result_id",
    "val_ce_input_out_of_scope": (
        "Материал {name} допустим только в submit: запросы poll и result материалы не передают"
    ),
    "val_ce_list_input_requires_each": "{name} — материал-список; раскройте его через $each, а не подставляйте напрямую",
    "val_ce_each_in_not_list_input": "$each.in указывает на {name}, но это не объявленный материал-список",
    "val_ce_each_shape_invalid": (
        "$each принимает либо item — для развёртывания элементов массива, либо сразу key и value — "
        "для развёртывания записей объекта; смешивать эти формы нельзя"
    ),
    "val_ce_each_position_mismatch": (
        "Форма $each не соответствует своей позиции: в позиции массива используйте item для развёртывания элементов, "
        "а в позиции объекта — key и value для развёртывания записей"
    ),
    "val_ce_each_alias_reserved": (
        "{name} — зарезервированная переменная в теле цикла, поэтому её нельзя использовать как псевдоним элемента $each"
    ),
    "val_ce_when_unknown_input": "$when указывает на {name}, но это не объявленный материал",
    "val_ce_input_not_referenced": (
        "Материал объявлен, но не используется в submit: он не отправляется и не может служить основанием для возможности"
    ),
    "val_ce_enum_map_variable_not_allowed": "{variable} нельзя сопоставить; переменные, доступные для сопоставления: {allowed}",
    "val_ce_default_variable_not_allowed": "У {variable} не может быть значения по умолчанию; это доступно для переменных: {allowed}",
    "val_ce_default_value_type_invalid": "Значение по умолчанию для {variable} должно иметь тип {expected}",
    "val_ce_default_value_not_in_enum_map": "Значение по умолчанию {value} для {variable} отсутствует в enum_maps; доступные значения: {allowed}",
    "val_ce_status_map_target_invalid": "Статус {target} не входит в {allowed}; семантику истечения срока сопоставьте с failed",
    "val_ce_capability_declared_without_input": (
        "Возможность {capability} объявлена, но submit не ссылается ни на один материал {source}, поэтому утверждение будет ложным"
    ),
    "val_ce_capability_input_without_declaration": (
        "submit ссылается на материал {source}, не объявляя {capability}, "
        "поэтому материал отправляется, а интерфейс скрывает эту возможность"
    ),
    "val_ce_capability_incoherent": "Возможность {capability} противоречит своей группе; требуется: {requirement}",
    "val_ce_capability_not_declared": "Объявите хотя бы одну возможность как true в capabilities: {allowed}",
    "val_ce_media_type_field_not_allowed": "{name} недоступно для определения с типом медиа {media_type}",
    "val_ce_artifact_extract_missing": "Нет пути извлечения результата; задайте хотя бы один: {keys}",
    "val_ce_jsonpath_not_a_string": "Путь извлечения должен быть строкой: {path_expression}",
    "val_ce_jsonpath_surrounding_whitespace": "Путь извлечения не должен содержать пробелов по краям: {path_expression}",
    "val_ce_jsonpath_missing_root": "Путь извлечения должен начинаться с $: {path_expression}",
    "val_ce_jsonpath_recursive_descent": (
        "Рекурсивный спуск недопустим в путях извлечения (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_union": (
        "Объединённые селекторы недопустимы в путях извлечения (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_slice_step": ("Шаги среза недопустимы в путях извлечения (символ {position}): {path_expression}"),
    "val_ce_jsonpath_function_extension": (
        "Расширения функций недопустимы в путях извлечения (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_root_reference": (
        "Фильтр не может ссылаться на корневой узел (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_filter_non_singular": (
        "В фильтре допустимы только одиночные запросы (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_regex_operator": (
        "Оператор сопоставления с регулярным выражением недопустим в путях извлечения (символ {position}): {path_expression}"
    ),
    "val_ce_jsonpath_syntax": "Синтаксическая ошибка в пути извлечения (символ {position}): {path_expression}",
    "val_ce_jsonpath_evaluation_failed": "Не удалось вычислить путь извлечения: {path_expression}",
    "val_ce_template_render_failed": "Не удалось сформировать запрос по шаблону: {detail}",
    "val_ce_auth_literal_credential": (
        "Похоже, это значение содержит настоящий секретный ключ: ссылайтесь на учётные данные через плейсхолдер "
        "api_key, иначе ключ будет раскрыт при экспорте или публикации определения"
    ),
    "val_ce_template_url_not_string": "Шаблон URL при подстановке должен давать строку",
    "val_ce_unknown_asset_encoding": "Неизвестная кодировка материала: {encoding}",
    "val_ce_enum_map_value_missing": "В enum_maps.{name} нет записи для «{value}»",
    "val_ce_template_text_variable_null": "Переменная {name} равна null, её нельзя вставить в текст",
    "val_ce_each_value_not_list": "$each.in указывает на {name}, значение которого во время выполнения не является списком",
    "val_ce_each_key_duplicate": "Объектная форма $each повторно создала ключ {field} в том же объекте, поэтому запрос не может передать все материалы",
    # ---- ComfyUI endpoint: node bindings and import routing ----
    "val_ce_comfyui_binding_required": (
        "{binding_key} должен быть привязан к узлу, иначе эндпоинт не сохранить: "
        "промпт определяет, что рисуется, а вывод — какой файл берётся"
    ),
    "val_ce_comfyui_binding_key_not_allowed": (
        "У эндпоинта {media_type} нет привязки {binding_key}; доступные ключи: {allowed}"
    ),
    "val_ce_comfyui_node_not_found": (
        "В workflow нет узла {node}: идентификаторы узлов меняются при каждом изменении workflow, "
        "поэтому импортируйте его заново и проверьте привязки узлов"
    ),
    "val_ce_comfyui_input_not_found": "У узла {node} нет входа с именем {input}",
    "val_ce_comfyui_input_is_link": (
        "Вход {input} узла {node} подключён к вышестоящему узлу, и при выполнении его значение будет перезаписано; "
        "привяжите вместо него поле с литеральным значением"
    ),
    "val_ce_comfyui_target_collision": (
        "Вход {input} узла {node} уже служит местом записи для {owner}, поэтому {binding_key} не может писать "
        "в то же поле: заполненное позже перезапишет прежнее"
    ),
    "val_ce_comfyui_class_type_mismatch": (
        "В записи узел {node} указан как {class_type}, а в workflow он теперь {actual}: повторное сопоставление "
        "определяет узлы по типу, поэтому при несовпадении привязка уедет к другому узлу. Импортируйте заново и подтвердите привязки"
    ),
    "val_ce_comfyui_consumer_not_fed": (
        "Поток от узла референса {node} не доходит до входа {input} узла {consumer}: при неверно записанном "
        "потребителе запрос с меньшим числом изображений переключит посторонний вход"
    ),
    "val_ce_comfyui_fps_conflict": (
        "У частоты кадров несколько противоречащих источников ({values}): для перевода длительности в число кадров "
        "нужна ровно одна частота, поэтому оставьте только один источник"
    ),
    "val_ce_comfyui_ui_format_workflow": (
        "Это workflow в формате UI ComfyUI, его нельзя отправить; экспортируйте его в ComfyUI через «Export (API)»"
    ),
    # ---- ComfyUI endpoint: node binding inference signals and hints ----
    "val_ce_infer_manual_binding": "Сохранённая привязка узла; используется цель, которую пользователь уже подтвердил",
    "val_ce_infer_title_marker": "В заголовке узла есть метка ARCREEL:",
    "val_ce_infer_external_node_family": "Параметрический узел {family}, в котором объявлен параметр {parameter}",
    "val_ce_infer_sampler_port_trace": "Прослежено от порта conditioning {port} узла {node}",
    "val_ce_infer_alias_with_class_type": "{class_type} обычно отвечает за эту семантику",
    "val_ce_infer_alias_only": "Имя входа {input} — распространённое название для этой семантики",
    "val_ce_infer_link_trace": "Его выход подаётся на вход {input} узла {consumer}",
    "val_ce_infer_class_type_tier": "{class_type} — более надёжный носитель среди кандидатов",
    "val_ce_infer_output_chain": "Он находится в цепочке, которая создаёт итоговый результат",
    "val_ce_infer_title_polarity": "Заголовок узла «{title}» указывает на эту полярность",
    "val_ce_infer_note_reference_consumer_unknown": (
        "Референсы подаются на вход {input} узла {node} ({class_type}); при меньшем числе изображений этот вход "
        "нельзя переключить, поэтому последнее изображение повторяется и заполняет оставшиеся слоты"
    ),
    "val_ce_infer_note_batch_size_above_one": (
        "Вход {input} узла {node} ({class_type}) равен {value}, поэтому одна отправка даёт несколько изображений; "
        "ArcReel не меняет это значение"
    ),
    "val_ce_infer_note_manual_only_node": (
        "Узел {node} — {class_type}; его {binding_keys} нельзя определить автоматически, поэтому привяжите их вручную"
    ),
    "val_ce_infer_note_computed_source": (
        "Кандидат для {binding_key} попадает на вход {input} узла {node}, но его значение вычисляется выше "
        "и не может быть записано; укажите его вручную"
    ),
    "val_ce_infer_note_rematched": "Записи {binding_key} повторно сопоставлены с узлами {nodes}",
    "val_ce_infer_note_binding_lost": (
        "У записей {binding_key} нет цели в новом workflow (раньше — узлы {nodes}); "
        "автоматическое определение выполнено заново, проверьте результат"
    ),
    "val_ce_infer_note_target_taken": (
        "Кандидат для {binding_key} попадает на вход {input} узла {node}, в который также пишет {others}; "
        "одно поле может нести только одну привязку, поэтому кандидат исключён из набора {binding_key}"
    ),
    "val_ce_poll_without_task_id": "Запрос опроса не ссылается на task_id; подтвердите, что это сделано намеренно",
    "val_ce_jsonpath_wildcard_order": (
        "{path_expression} содержит подстановочный знак: для объекта берётся только первый элемент, "
        "а порядок ключей в предпросмотре и на сервере может различаться"
    ),
    # ---- market source checks ----
    "val_market_index_unreadable": "Не удалось прочитать файл индекса: {detail}",
    "val_market_unsupported_schema_version": "Версия схемы индекса schema_version {version} новее поддерживаемой {supported}",
    "val_market_missing_field": "Отсутствует обязательное поле: {field}",
    "val_market_invalid_type": "Неверный тип; ожидается {expected}",
    "val_market_invalid_value": "Значение нарушает ограничение {keyword}: {constraint}",
    "val_market_slug_invalid": (
        "Недопустимый slug «{value}»: используйте строчные буквы, цифры и дефисы, "
        "начинайте с буквы или цифры, не длиннее 64 символов"
    ),
    "val_market_slug_duplicate": "Slug «{slug}» встречается в этом источнике маркета больше одного раза",
    "val_market_slug_directory_mismatch": "Slug «{slug}» не совпадает с каталогом определения «{directory}»",
    "val_market_path_not_relative": (
        "«{value}» должен быть относительным путём внутри источника маркета: "
        "без ведущего / и схемы, без .., и он не должен выходить за пределы источника"
    ),
    "val_market_file_missing": "Указанный файл не существует: {value}",
    "val_market_symlink_not_allowed": (
        "«{value}» — символическая ссылка: клиенты, загружающие через GitHub raw, получат текст пути цели, "
        "поэтому используйте обычный файл или каталог"
    ),
    "val_market_icon_format_invalid": "Значок должен быть читаемым PNG, WebP или SVG, а расширение — совпадать с содержимым",
    "val_market_icon_too_large": "Размер значка {size} байт, что превышает предел в {limit} байт",
    "val_market_icon_not_square": "Значок должен быть квадратным; сейчас {width}×{height}",
    "val_market_icon_ambiguous": "В каталоге записи может быть только один файл значка; найдено: {icons}",
    "val_market_definition_unreadable": "Не удалось прочитать определение: {detail}",
    "val_market_definition_invalid": "Определение не прошло проверку ({code}): {detail}",
    "val_market_projection_mismatch": (
        "Поле индекса {field} равно «{index_value}», что отличается от «{definition_value}» в meta определения"
    ),
    "val_market_min_app_version_invalid": "min_app_version «{value}» не соответствует semver (x.y.z)",
    "val_market_detail_meta_not_object": "В определении нет объекта meta",
    "val_market_cli_check_passed": "Проверка источника маркета пройдена: {directory}",
    "val_market_cli_check_failed": "Проверка источника маркета не пройдена; найдено проблем: {count}",
    "val_market_cli_generate_written": "Записан {path} (записей: {count})",
    "val_market_cli_generate_failed": "Не удалось сгенерировать индекс; найдено проблем: {count}",
    "arch_provider_asset_links_not_restored": "Проект импортирован, но {count} ссылок на библиотеку материалов сохранить не удалось; при генерации эти изображения отправляются как изображения. При необходимости загрузите их заново или выберите из библиотеки материалов",
    # Draft diagnostics; Agent reports retain their original messages.
    "draft_violation_source_text_empty": "{label}: source_text пуст. Скопируйте исходный фрагмент для этой видеоединицы.",
    "draft_violation_source_text_not_verbatim": "{label}: source_text не совпадает с оригиналом: {excerpt}. Скопируйте текст без пересказа и перевода.",
    "draft_violation_fullwidth_braces": "{label}: полноширинные скобки в {excerpt}. Используйте фигурные скобки ASCII для реплик и закадрового голоса.",
    "draft_violation_malformed_mention": "{label}: неверная ссылка на ресурс {excerpt}. Используйте `@[имя ресурса]` с непустым именем и парными скобками.",
    "draft_violation_dialogue_line_syntax": "{label}: неверный синтаксис реплики в {excerpt}. Укажите говорящего и реплику в фигурных скобках ASCII, иначе текст станет описанием кадра.",
    "draft_violation_unclosed_brace": "{label}: незакрытая скобка в {excerpt}. Закройте каждую пару фигурных скобок ASCII.",
    "draft_violation_braces_in_description": "{label}: скобки в описании кадра {excerpt}. Фигурные скобки предназначены только для реплик и закадрового голоса; вложенность запрещена.",
    "draft_violation_empty_text": "{label}: текст пуст. Добавьте описание кадра и необходимые реплики.",
    "draft_violation_blank_description": "{label}: нет описания кадра. Одних реплик и закадрового голоса недостаточно для изображения.",
    "draft_violation_unregistered_asset": "{label}: {field} ссылается на незарегистрированные ресурсы: {names}. Используйте зарегистрированные имена или добавьте решения в new_assets.",
    "draft_violation_unregistered_speaker": "{label}: говорящие не зарегистрированы как персонажи: {names}. Для выбора референсного аудио нужны зарегистрированные персонажи.",
    "draft_violation_unregistered_speaker_derivative": "{label}: незарегистрированные варианты говорящего: {names}. Зарегистрируйте вариант или используйте имя основного персонажа.",
    "draft_violation_refs_over_limit": "{label}: число референсных изображений {count} превышает лимит {limit}. Опишите второстепенных персонажей в фоне без ссылок `@`.",
    "draft_violation_speakers_over_limit": "{label}: число говорящих {count} превышает лимит референсного аудио модели {limit}. Разделите кадры по говорящим, соблюдая лимит каждой видеоединицы.",
    "draft_violation_dialogue_overload": "{label}: реплики требуют около {spoken} с, превышая {duration} с (допуск {tolerance}, максимум {budget} с). Выберите большую длительность, разделите единицу или сократите реплики.",
    "draft_violation_dialogue_line_count_changed": "{label}: число реплик изменилось с {before} на {after}. При написании промптов реплики должны сохраняться дословно.",
    "draft_violation_dialogue_rewritten": "{label}: реплика {index} переписана ({before} → {after}). При написании промптов сохраняйте реплики дословно.",
    "draft_violation_duration_off_tier": "{label}: длительность {duration} с не входит в допустимые значения {tiers}. Выберите допустимую длительность; если подтверждённое значение больше не поддерживается, измените ссылки или заново разделите план сценария.",
    "draft_violation_invalid_segment_id": "{label}: segment_id должен иметь формат E{episode}S## и соответствовать номеру эпизода.",
    "draft_violation_duplicate_segment_id": "Повторяющиеся segment_id: {ids}. ID каждого кадра должен быть уникален в эпизоде.",
    "draft_violation_blank_novel_text": "{label}: novel_text пуст. Каждый кадр должен содержать закадровый текст, дословно взятый из оригинала.",
    "draft_violation_novel_text_coverage": "novel_text кадров не покрывает оригинал полностью, дословно и по порядку. Источник: {source}. Скопируйте и соедините фрагменты по порядку; если диапазон источника черновика указан неверно, сначала исправьте его.",
    "draft_violation_schema_invalid": "{path} черновика не соответствует структуре результата: {detail}. Проверьте обязательные поля, типы и значения.",
    "draft_violation_formal_revision_conflict": "Утверждённое содержимое изменилось во время генерации. Результат сохранён как черновик. Объедините его с актуальным содержимым перед продвижением.",
    "draft_violation_draft_revision_conflict": "Черновик ожидает обработки или изменился во время генерации. Продвиньте, удалите или объедините его перед повтором.",
    "draft_violation_unit_count_changed": "При написании промптов получено {actual} видеоединиц вместо {expected}. На этом этапе нельзя объединять, разделять, добавлять или удалять единицы.",
    "draft_violation_formal_revision_missing": "В черновике нет базовой версии сценария на момент генерации; безопасное продвижение невозможно. Создайте новый черновик.",
    "draft_violation_new_asset_invalid": "{label}: неверная структура. Проверьте массив, обязательные поля и решение.",
    "draft_violation_new_asset_name_invalid": "{label}: недопустимое имя {field}: {name}. Используйте допустимое имя ресурса.",
    "draft_violation_new_asset_name_taken": "{label}: имя {name} конфликтует с {existing} ({asset_type}). Укажите другое имя.",
    "draft_violation_new_asset_derivative_not_character": "{label}: варианты доступны только персонажам. Используйте register, merge или skip.",
    "draft_violation_new_asset_merge_cycle": "{label}: цикл объединения: {chain}. Исправьте target или используйте register / skip.",
    "draft_violation_new_asset_target_unresolved": "{label}: цель {target} не является зарегистрированным или новым ресурсом того же типа ({asset_type}). Исправьте target или используйте register / skip.",
    "draft_violation_new_asset_base_unresolved": "{label}: основа {target} не является зарегистрированным или новым персонажем. Исправьте target или используйте register / skip.",
    "draft_violation_mixed_speech": "{label} ({location}): Нельзя смешивать реплики и закадровый голос. Разделите видеоединицу.",
    "draft_violation_needs_replan": "{label} ({location}): Сочетание речи требует перепланирования. Разделите видеоединицу.",
    "draft_violation_parse_failed": "{label} ({location}): Не удалось разобрать речь. Исправьте формат входных данных.",
    "draft_violation_empty_speaker": "{label} ({location}): У реплики нет говорящего. Укажите персонажа.",
}
