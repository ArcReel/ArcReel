# 验证记录 — ComfyUI `duration` 语义绑定键

所有命令在 worktree `/Volumes/exSDCARD/MyWorks/ArcReel/.worktrees/comfyui-duration-binding` 内执行，Python 用 `uv`。

## 改动文件（8 个，相对仓库根）

- `packages/arcreel-market-core/src/arcreel_market_core/comfyui/bindings.py` — `VIDEO_BINDING_KEYS` 新增 `"duration"`（紧邻 `frames`/`fps`），附密集中文注释。
- `packages/arcreel-market-core/src/arcreel_market_core/comfyui/schema.json` — `$defs.bindings.properties` 新增 `duration`，items `$ref` `#/$defs/steppedTarget`。
- `lib/custom_provider/comfyui/request_builder.py` — 新增 `_write_duration`，`BuiltWorkflow` 增 `duration: int | None` 字段并在 `build_workflow` 接线（绕过 `keeps_its_own_frame_count` 的 `driven` 开关，直收 `duration_seconds`）。
- `lib/custom_provider/comfyui/capabilities.py` — `duration_is_fixed` 改为 `frames` 与 `duration` 都无绑定才为 True；中文注释说明 frames↔fps↔seconds 重建仍只认 `frames`。
- `packages/arcreel-market-core/tests/comfyui/test_comfyui_definition_validator.py` — `TestDurationBinding`（通过 / 图像端点拒 / 碰撞）。
- `tests/unit/lib/custom_provider/comfyui/test_comfyui_request_builder.py` — `TestDuration`（直写 / 取整 / 向下对齐 / 下限一个 step / None 不写 / 未绑不写）。
- `tests/unit/lib/custom_provider/comfyui/test_comfyui_capabilities.py` — `TestDurationIsFixedAcrossBothTimeAxisKeys`（只绑 duration / 只绑 frames / 都不绑 / 不污染帧数重建）。

## 命令与结果

### 1. 子包 comfyui 全套

```
cd packages/arcreel-market-core && uv run python -m pytest tests/comfyui/ -q
```
结果：`97 passed`。

新增用例确认实际执行（`-v`）：
```
uv run python -m pytest tests/comfyui/test_comfyui_definition_validator.py::TestDurationBinding \
  tests/comfyui/test_comfyui_definition_schema.py::test_binding_keys_are_the_closed_set_in_the_schema -v
```
→ `4 passed`，收集到：
- `TestDurationBinding::test_a_video_definition_with_a_duration_binding_passes`
- `TestDurationBinding::test_a_duration_binding_on_an_image_endpoint_is_refused`
- `TestDurationBinding::test_a_duration_target_colliding_with_another_write_key_is_refused`
- `test_binding_keys_are_the_closed_set_in_the_schema`（契约断言仍绿：schema bindings 键集 == `VIDEO_BINDING_KEYS`，`IMAGE_BINDING_KEYS ⊂ VIDEO_BINDING_KEYS`）

### 2. lib request_builder + capabilities

```
uv run python -m pytest tests/unit/lib/custom_provider/comfyui/test_comfyui_request_builder.py \
  tests/unit/lib/custom_provider/comfyui/test_comfyui_capabilities.py -q
```
结果：`100 passed, 1 warning`。

新增用例确认实际执行（`-v`）→ `10 passed`，收集到：
- `TestDuration::test_the_seconds_are_written_as_is_without_a_step`
- `TestDuration::test_a_fractional_second_is_rounded_to_an_int`
- `TestDuration::test_a_stepped_duration_is_aligned_down_to_a_multiple_of_the_step`
- `TestDuration::test_a_duration_below_one_step_is_floored_at_one_step`
- `TestDuration::test_a_none_duration_writes_nothing`
- `TestDuration::test_an_unbound_duration_key_writes_nothing`
- `TestDurationIsFixedAcrossBothTimeAxisKeys::test_only_duration_bound_is_not_fixed`
- `TestDurationIsFixedAcrossBothTimeAxisKeys::test_only_frames_bound_is_not_fixed`
- `TestDurationIsFixedAcrossBothTimeAxisKeys::test_neither_bound_is_fixed`
- `TestDurationIsFixedAcrossBothTimeAxisKeys::test_duration_does_not_feed_the_frames_reconstruction`

### 3. 完整闸门（按改动选域）

```
uv run python scripts/gate.py --changed
```
命中域：`backend market-core tests conventions`。

- `ruff check` / `ruff format --check`：通过（期间修了一处 `int(round(...))` 冗余 cast 与 3 个测试文件的换行格式）。
- `basedpyright --warnings`：`0 errors, 0 warnings, 0 notes`。
- `lint-imports`、`deptry`：通过。
- 子包 pytest、`audit_tests.py --check`、`audit_conventions.py --check`：通过。

闸门里 `tests/unit/server/` 有 7 失败 + 7 错误，全部同因：
`RuntimeError: SECURITY: 父进程 os.environ 含 provider 密钥: ['ANTHROPIC_API_KEY']`。
这是本沙箱父进程环境里带了 `ANTHROPIC_API_KEY` 触发的安全守卫，与本次改动无关（这些测试都在
`tests/unit/server/`，未改任何相关文件）。把该变量从环境里摘掉后复跑：

```
env -u ANTHROPIC_API_KEY uv run python scripts/gate.py --changed
```
结果：`[gate] backend market-core tests conventions 全部通过，用时 194s`（含 `tests` 域 `689 passed`、
backend 两套 pytest 全绿、审计脚本 `0 处违规`）。单测佐证：
`env -u ANTHROPIC_API_KEY uv run python -m pytest tests/unit/server/test_app_module.py::TestAppModule::test_lifespan_starts_and_stops_worker` → `1 passed`。

## 结论

设计决定 1–5 全部落地，新增测试实际执行并通过，完整闸门在干净环境（无父进程 provider 密钥）下全绿。
