# Grok 供应商多问题修复实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复 Grok 供应商的四个问题：上传图片过大导致 gRPC 4MB 报错、图片并发异常、参考图只用第一张、drama 模式比例不对。

**Architecture:** 在上传入口压缩用户图片（JPEG + 限分辨率），Grok 图片后端改用多图编辑 API（`image_urls`）以同时支持多参考图和 aspect_ratio 生效，并在 worker 中增加 pool 配置日志以排查并发问题。

**Tech Stack:** Python, Pillow, xai_sdk, pytest

---

## 文件映射

| 文件 | 动作 | 职责 |
|------|------|------|
| `lib/image_utils.py` | 改动 | 新增 `compress_image_bytes()` |
| `tests/test_image_utils.py` | 新建 | `compress_image_bytes()` 单元测试 |
| `server/routers/files.py` | 改动 | 上传入口改用压缩 + `.jpg` 后缀 |
| `lib/data_validator.py` | 改动 | `ALLOWED_ROOT_ENTRIES` 加 `style_reference.jpg` |
| `server/services/project_archive.py` | 改动 | `style_reference.png` → `.jpg` |
| `lib/image_backends/grok.py` | 改动 | `image_url` → `image_urls` + aspect_ratio 校验 |
| `tests/test_image_backends/test_grok.py` | 改动 | 适配 `image_urls` |
| `lib/generation_worker.py` | 改动 | 增加 pool 配置日志 |

---

### Task 1: 图片压缩函数 — TDD

**Files:**
- Modify: `lib/image_utils.py`
- Create: `tests/test_image_utils.py`

- [ ] **Step 1: 编写 `compress_image_bytes` 失败测试**

```python
# tests/test_image_utils.py
"""image_utils 单元测试。"""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.image_utils import compress_image_bytes


class TestCompressImageBytes:
    """compress_image_bytes 测试。"""

    def _make_png(self, width: int, height: int) -> bytes:
        """生成指定尺寸的 PNG 字节。"""
        img = Image.new("RGB", (width, height), color="red")
        buf = BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()

    def test_small_image_unchanged_dimensions(self):
        """小图（长边 < 2048）不缩放，但仍转为 JPEG。"""
        raw = self._make_png(800, 600)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert img.size == (800, 600)

    def test_large_image_resized(self):
        """大图（长边 > 2048）缩放到长边 2048。"""
        raw = self._make_png(4096, 3072)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert img.format == "JPEG"
        assert max(img.size) == 2048
        # 等比缩放
        assert img.size == (2048, 1536)

    def test_portrait_large_image(self):
        """竖图大图也正确缩放。"""
        raw = self._make_png(2000, 4000)
        result = compress_image_bytes(raw)
        img = Image.open(BytesIO(result))
        assert max(img.size) == 2048
        assert img.size == (1024, 2048)

    def test_rgba_converted_to_rgb(self):
        """RGBA 图片转为 RGB（JPEG 不支持 alpha）。"""
        img = Image.new("RGBA", (100, 100), color=(255, 0, 0, 128))
        buf = BytesIO()
        img.save(buf, format="PNG")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.mode == "RGB"

    def test_jpeg_input(self):
        """JPEG 输入也能正常处理。"""
        img = Image.new("RGB", (500, 500), color="blue")
        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_webp_input(self):
        """WebP 输入也能正常处理。"""
        img = Image.new("RGB", (500, 500), color="green")
        buf = BytesIO()
        img.save(buf, format="WEBP")
        result = compress_image_bytes(buf.getvalue())
        out = Image.open(BytesIO(result))
        assert out.format == "JPEG"

    def test_invalid_input_raises(self):
        """非图片字节抛出 ValueError。"""
        with pytest.raises(ValueError, match="Invalid image"):
            compress_image_bytes(b"not an image")

    def test_output_smaller_than_input(self):
        """压缩后体积应显著减小。"""
        raw = self._make_png(3000, 2000)
        result = compress_image_bytes(raw)
        assert len(result) < len(raw)
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_image_utils.py -v`
Expected: FAIL — `ImportError: cannot import name 'compress_image_bytes'`

- [ ] **Step 3: 实现 `compress_image_bytes`**

在 `lib/image_utils.py` 的 `convert_image_bytes_to_png` 函数下方添加：

```python
_MAX_LONG_EDGE = 2048
_JPEG_QUALITY = 85


def compress_image_bytes(
    content: bytes,
    *,
    max_long_edge: int = _MAX_LONG_EDGE,
    quality: int = _JPEG_QUALITY,
) -> bytes:
    """
    将任意图片字节压缩为 JPEG：等比缩放到长边不超过 max_long_edge，
    quality 控制 JPEG 压缩质量。

    Raises:
        ValueError: if the input bytes are not a valid image.
    """
    try:
        with Image.open(BytesIO(content)) as img:
            img = ImageOps.exif_transpose(img)
            if img.mode in ("RGBA", "P", "LA"):
                img = img.convert("RGB")
            elif img.mode != "RGB":
                img = img.convert("RGB")

            w, h = img.size
            long_edge = max(w, h)
            if long_edge > max_long_edge:
                scale = max_long_edge / long_edge
                new_w = int(w * scale)
                new_h = int(h * scale)
                img = img.resize((new_w, new_h), Image.LANCZOS)

            out = BytesIO()
            img.save(out, format="JPEG", quality=quality, optimize=True)
            return out.getvalue()
    except Exception as e:
        raise ValueError("Invalid image") from e
```

同时在文件顶部确认 `from io import BytesIO` 已导入（已有）。

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_image_utils.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add lib/image_utils.py tests/test_image_utils.py
git commit -m "feat: 新增 compress_image_bytes 函数，支持 JPEG 压缩 + 分辨率限制"
```

---

### Task 2: 上传入口 — 大于 2MB 时压缩

**Files:**
- Modify: `server/routers/files.py:92-138` — 通用上传逻辑
- Modify: `server/routers/files.py:486-541` — 风格参考图上传
- Modify: `lib/data_validator.py:50` — ALLOWED_ROOT_ENTRIES
- Modify: `server/services/project_archive.py:492` — 归档路径

**策略**：上传图片 > 2MB 时压缩为 JPEG（`.jpg`），≤ 2MB 直接保存原始内容（保留原格式后缀）。

- [ ] **Step 1: 修改通用上传逻辑**

在 `server/routers/files.py` 中：

1. 导入 `compress_image_bytes`：

```python
from lib.image_utils import compress_image_bytes
```

2. 增加阈值常量（文件顶部常量区）：

```python
_COMPRESS_THRESHOLD = 2 * 1024 * 1024  # 2MB
```

3. 替换图片处理部分（原 lines 132-138）。移除 `convert_image_bytes_to_png` 调用，改为仅在大于 2MB 时压缩：

```python
        content = await file.read()
        if upload_type in ("character", "character_ref", "clue", "storyboard"):
            if len(content) > _COMPRESS_THRESHOLD:
                try:
                    content = compress_image_bytes(content)
                except ValueError:
                    raise HTTPException(status_code=400, detail="无效的图片文件，无法解析")
                # 压缩后替换文件名后缀为 .jpg
                filename = Path(filename).with_suffix(".jpg").name
```

≤ 2MB 的图片直接保存原始内容，文件名后缀保留上方分支中已有的 `.png`（即原有逻辑不变）。

- [ ] **Step 2: 修改风格参考图上传**

在 `server/routers/files.py` 的 `upload_style_image` 函数中，同样按 2MB 阈值处理：

```python
        content = await file.read()
        if len(content) > _COMPRESS_THRESHOLD:
            try:
                content = compress_image_bytes(content)
            except ValueError:
                raise HTTPException(status_code=400, detail="无效的图片文件，无法解析")
            style_filename = "style_reference.jpg"
        else:
            style_filename = f"style_reference{Path(file.filename).suffix.lower() or '.png'}"

        output_path = project_dir / style_filename
        with open(output_path, "wb") as f:
            f.write(content)
```

后续 `project_data["style_image"]` 和返回值使用 `style_filename` 变量。

同样更新 `delete_style_image` 函数，改为尝试删除两种后缀：

```python
        for suffix in (".jpg", ".png"):
            image_path = project_dir / f"style_reference{suffix}"
            if image_path.exists():
                image_path.unlink()
                break
```

- [ ] **Step 3: 更新 data_validator 和 project_archive**

`lib/data_validator.py` line 50 — 在 `ALLOWED_ROOT_ENTRIES` 中添加 `.jpg` 变体（保留 `.png` 兼容旧项目）：

```python
    ALLOWED_ROOT_ENTRIES = {
        "project.json",
        "style_reference.png",
        "style_reference.jpg",
        "source",
        ...
    }
```

`server/services/project_archive.py` line 492 — 归档修复逻辑需同时处理两种后缀。将 `canonical_rel` 改为检查实际存在的文件：先查 `.jpg`，不存在则查 `.png`。如果修改 `_repair_path_to_canonical` 逻辑过于侵入，可保持现状不改此文件（旧项目仍为 `.png`，新项目的 `style_image` 字段已正确指向实际文件）。

- [ ] **Step 4: 运行现有测试确认无回归**

Run: `uv run python -m pytest tests/ -v -k "upload or style or archive or validator or fingerprint" --no-header`
Expected: 全部 PASS（部分测试可能需要适配后缀变化）

- [ ] **Step 5: 提交**

```bash
git add server/routers/files.py lib/data_validator.py
git commit -m "feat: 上传图片大于 2MB 时压缩为 JPEG + 限制长边 2048px"
```

---

### Task 3: Grok 图片后端 — 多参考图 + 比例校验

**Files:**
- Modify: `lib/image_backends/grok.py:52-86`
- Modify: `tests/test_image_backends/test_grok.py`

- [ ] **Step 1: 更新测试 — I2I 改用 `image_urls`**

修改 `tests/test_image_backends/test_grok.py` 的 `TestGenerateI2I` 类：

```python
class TestGenerateI2I:
    async def test_i2i_sends_image_urls(self, backend, tmp_path):
        """I2I 将参考图转为 data URI 列表传给 image_urls。"""
        ref_image = tmp_path / "ref.png"
        ref_image.write_bytes(b"\x89PNG\r\n\x1a\nfake_png_data")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/edited.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="Make it darker",
                output_path=output,
                reference_images=[ReferenceImage(path=str(ref_image), label="base")],
            )
            result = await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_urls" in call_kwargs
        assert "image_url" not in call_kwargs
        assert len(call_kwargs["image_urls"]) == 1
        assert call_kwargs["image_urls"][0].startswith("data:image/png;base64,")
        assert result.provider == "grok"

    async def test_i2i_multiple_refs(self, backend, tmp_path):
        """多张参考图全部通过 image_urls 传递。"""
        ref1 = tmp_path / "ref1.png"
        ref1.write_bytes(b"\x89PNG\r\n\x1a\nfake1")
        ref2 = tmp_path / "ref2.jpg"
        ref2.write_bytes(b"\xff\xd8\xff\xe0fake2")

        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/merged.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="Merge subjects",
                output_path=output,
                reference_images=[
                    ReferenceImage(path=str(ref1)),
                    ReferenceImage(path=str(ref2)),
                ],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert len(call_kwargs["image_urls"]) == 2

    async def test_i2i_skips_missing_ref(self, backend, tmp_path):
        """参考图不存在时退化为 T2I。"""
        output = tmp_path / "output.png"
        mock_response = MagicMock()
        mock_response.respect_moderation = True
        mock_response.url = "https://example.com/generated.png"
        backend._client.image.sample = AsyncMock(return_value=mock_response)

        fake_image_bytes = b"\x89PNG\r\n\x1a\n"

        with patch("lib.image_backends.grok.httpx.AsyncClient") as MockHttpClient:
            mock_http = AsyncMock()
            MockHttpClient.return_value.__aenter__ = AsyncMock(return_value=mock_http)
            MockHttpClient.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_resp = MagicMock()
            mock_resp.content = fake_image_bytes
            mock_resp.raise_for_status = MagicMock()
            mock_http.get = AsyncMock(return_value=mock_resp)

            request = ImageGenerationRequest(
                prompt="A cat",
                output_path=output,
                reference_images=[ReferenceImage(path="/nonexistent/ref.png")],
            )
            await backend.generate(request)

        call_kwargs = backend._client.image.sample.call_args.kwargs
        assert "image_urls" not in call_kwargs
        assert "image_url" not in call_kwargs
```

新增 aspect_ratio 校验测试：

```python
class TestAspectRatioValidation:
    def test_supported_ratios_pass_through(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        for ratio in ("16:9", "9:16", "4:3", "3:4"):
            assert _validate_aspect_ratio(ratio) == ratio

    def test_unsupported_ratio_falls_back(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        # 1:1 不支持，应映射到最近的支持比例
        result = _validate_aspect_ratio("1:1")
        assert result in ("16:9", "9:16", "4:3", "3:4")

    def test_3_2_falls_back(self):
        from lib.image_backends.grok import _validate_aspect_ratio

        result = _validate_aspect_ratio("3:2")
        assert result in ("16:9", "9:16", "4:3", "3:4")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python -m pytest tests/test_image_backends/test_grok.py -v`
Expected: FAIL — 旧测试断言 `image_url`（单数），新测试断言 `image_urls`（复数）

- [ ] **Step 3: 实现 Grok 图片后端改动**

替换 `lib/image_backends/grok.py` 的 `generate` 方法中 I2I 逻辑和新增校验函数：

在文件顶部常量区（`DEFAULT_MODEL` 下方）添加：

```python
_SUPPORTED_ASPECT_RATIOS = {"16:9", "9:16", "4:3", "3:4"}

_ASPECT_RATIO_FALLBACK: dict[str, str] = {
    "1:1": "4:3",
    "3:2": "16:9",
    "2:3": "9:16",
}
```

新增校验函数（在 `_map_image_size_to_resolution` 前）：

```python
def _validate_aspect_ratio(aspect_ratio: str) -> str:
    """校验 aspect_ratio 是否在 Grok 支持列表中，不支持则映射到最近值。"""
    if aspect_ratio in _SUPPORTED_ASPECT_RATIOS:
        return aspect_ratio
    fallback = _ASPECT_RATIO_FALLBACK.get(aspect_ratio)
    if fallback:
        logger.warning("Grok 不支持 aspect_ratio=%s，映射为 %s", aspect_ratio, fallback)
        return fallback
    logger.warning("Grok 不支持 aspect_ratio=%s，回退为 16:9", aspect_ratio)
    return "16:9"
```

替换 `generate` 方法中的 I2I 部分（lines 52-86）：

```python
    async def generate(self, request: ImageGenerationRequest) -> ImageGenerationResult:
        """生成图片（T2I 或 I2I）。"""
        generate_kwargs: dict = {
            "prompt": request.prompt,
            "model": self._model,
            "aspect_ratio": _validate_aspect_ratio(request.aspect_ratio),
            "resolution": _map_image_size_to_resolution(request.image_size),
        }

        # I2I：将所有参考图转为 base64 data URI 列表
        if request.reference_images:
            data_uris = []
            for ref in request.reference_images:
                ref_path = Path(ref.path)
                if ref_path.exists():
                    data_uris.append(image_to_base64_data_uri(ref_path))
            if data_uris:
                generate_kwargs["image_urls"] = data_uris
                logger.info("Grok I2I 模式: %d 张参考图", len(data_uris))

        logger.info("Grok 图片生成开始: model=%s", self._model)
        response = await self._client.image.sample(**generate_kwargs)

        # 审核检查
        if not response.respect_moderation:
            raise RuntimeError("Grok 图片生成被内容审核拒绝")

        # 下载图片到本地
        await _download_image(response.url, request.output_path)

        logger.info("Grok 图片下载完成: %s", request.output_path)

        return ImageGenerationResult(
            image_path=request.output_path,
            provider=PROVIDER_GROK,
            model=self._model,
            image_uri=response.url,
        )
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python -m pytest tests/test_image_backends/test_grok.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add lib/image_backends/grok.py tests/test_image_backends/test_grok.py
git commit -m "fix: Grok 图片后端改用 image_urls 支持多参考图，修复 I2I 比例被忽略"
```

---

### Task 4: Generation Worker — 增加 pool 配置日志

**Files:**
- Modify: `lib/generation_worker.py:128-149`

- [ ] **Step 1: 在 `_load_pools_from_db` 增加日志**

在 `_load_pools_from_db` 函数末尾（return 前）添加：

```python
    logger.info(
        "从 DB 加载供应商池配置: %s",
        {pid: (p.image_max, p.video_max) for pid, p in pools.items()},
    )
    return pools
```

- [ ] **Step 2: 在 `__init__` 增加初始 pool 日志**

在 `GenerationWorker.__init__` 的 `self._pools` 赋值后（line 186 之后）添加：

```python
        logger.info(
            "Worker 初始池配置: %s",
            {pid: (p.image_max, p.video_max) for pid, p in self._pools.items()},
        )
```

- [ ] **Step 3: 在 `_get_or_create_pool` 的 fallback 路径增强日志**

当前 line 241 已有 warning 日志。确认 `_get_or_create_pool` 的 warning 日志包含足够信息（已满足，无需改动）。

- [ ] **Step 4: 运行 worker 测试确认无回归**

Run: `uv run python -m pytest tests/test_generation_worker_module.py -v`
Expected: 全部 PASS

- [ ] **Step 5: 提交**

```bash
git add lib/generation_worker.py
git commit -m "fix: Generation Worker 增加 pool 配置日志，便于排查并发问题"
```

---

### Task 5: Lint + 全量测试

**Files:** 无新改动

- [ ] **Step 1: Ruff lint + format**

Run: `uv run ruff check lib/image_utils.py lib/image_backends/grok.py lib/generation_worker.py server/routers/files.py lib/data_validator.py server/services/project_archive.py && uv run ruff format --check lib/image_utils.py lib/image_backends/grok.py lib/generation_worker.py server/routers/files.py lib/data_validator.py server/services/project_archive.py`

Expected: 无错误。如有，修复后重新运行。

- [ ] **Step 2: 运行全量测试**

Run: `uv run python -m pytest tests/ -v --no-header`

Expected: 全部 PASS。如有失败，修复后重新运行。

- [ ] **Step 3: 如有修复则提交**

```bash
git add -A
git commit -m "chore: lint 修复"
```
