"""ENDPOINT_REGISTRY 完整性与工具函数单测。"""

from __future__ import annotations

import pytest

from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    endpoint_spec_to_dict,
    endpoint_to_media_type,
    get_endpoint_spec,
    infer_endpoint,
    list_endpoints_by_media_type,
    validate_video_caps_declaration,
)


class TestRegistry:
    def test_endpoint_count(self):
        assert set(ENDPOINT_REGISTRY.keys()) == {
            "openai-chat",
            "gemini-generate",
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "gemini-image",
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
            "dashscope-image",
            "dashscope-async-video",
            "minimax-image",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-image",
            "kling-video",
            "openai-tts",
        }

    def test_each_spec_has_required_fields(self):
        for key, spec in ENDPOINT_REGISTRY.items():
            assert spec.key == key
            assert spec.media_type in {"text", "image", "video", "audio"}
            assert spec.family in {"openai", "google", "newapi", "v2", "ark", "vidu", "dashscope", "minimax", "kling"}
            # 注册表里的都是内置端点，来源恒为 builtin；用户端点不进注册表，由 ce- 键现构造。
            assert spec.source == "builtin"
            # 显示名两种来源恰有其一：Python 内置走 i18n key，声明式端点走定义里的 meta.name。
            if spec.kind == "declarative":
                assert spec.display_name_key == ""
                assert spec.display_name
            else:
                assert spec.display_name_key.startswith("endpoint_")
                assert spec.display_name is None
            assert callable(spec.build_backend)
            assert spec.request_method == "POST"
            assert spec.request_path_template.startswith("/")

    def test_endpoint_spec_to_dict_drops_closure(self):
        spec = ENDPOINT_REGISTRY["openai-chat"]
        d = endpoint_spec_to_dict(spec)
        assert "build_backend" not in d
        assert d == {
            "key": "openai-chat",
            "media_type": "text",
            "family": "openai",
            "kind": "python",
            "display_name_key": "endpoint_openai_chat_display",
            # 显示名只有声明式端点从定义里带出，Python 内置按 display_name_key 取 i18n 文案
            "display_name": None,
            "source": "builtin",
            "request_method": "POST",
            "request_path_template": "/v1/chat/completions",
            "image_capabilities": None,
            # 未声明的 endpoint cap 序列化为 None（resolver fallthrough 到 backend caps）
            "video_max_reference_images": None,
            "end_image_capable": False,
            "reference_audio_capable": False,
        }

    def test_new_video_endpoints_have_unset_cap(self):
        """v2/ark/vidu/dashscope/minimax/kling 不在 endpoint 维度声明上限，由 resolver 调 backend 纯 caps 函数读取。"""
        for key in (
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
            "dashscope-async-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video",
        ):
            assert ENDPOINT_REGISTRY[key].video_max_reference_images is None
        # 既有显式 int 保留，行为零变化
        assert ENDPOINT_REGISTRY["openai-video"].video_max_reference_images == 1
        assert ENDPOINT_REGISTRY["newapi-video"].video_max_reference_images is None

    def test_video_caps_declaration_bindings(self):
        """每个 video endpoint 选对了上限来源：None-cap 的绑 caps_fn、显式 int 的不绑。

        全注册表 XOR/非负不变式由 endpoints.py 的 module-load `_validate_registry()`
        在 import 期保证（违反则本文件根本 import 不进来），故此处只断言「具体哪个 endpoint 选了哪条
        路径」——这是 XOR 校验抓不到的（换机制仍满足 XOR），是真正的回归护栏。"""
        # None-cap 的 video endpoint 必须绑定纯 caps 函数
        for key in (
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
            "dashscope-async-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video",
        ):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is not None
        # 显式 int 的 video endpoint 不应再绑 caps 函数
        for key in ("openai-video",):
            assert ENDPOINT_REGISTRY[key].video_caps_for_model is None

    def test_dashscope_caps_fn_reads_per_model_limit_without_client(self):
        """dashscope-async-video 的 caps_fn 是纯函数：按 model_id 返回真实参考图上限
        （happyhorse-r2v=9 / wan2.7-r2v=5），resolver 据此解析而无需构造 backend / api_key。"""
        caps_fn = ENDPOINT_REGISTRY["dashscope-async-video"].video_caps_for_model
        assert caps_fn is not None
        assert caps_fn("happyhorse-1.0-r2v").max_reference_images == 9
        assert caps_fn("wan2.7-r2v").max_reference_images == 5

    def test_minimax_declarative_endpoints_expose_definition_capabilities(self):
        s2v = ENDPOINT_REGISTRY["minimax-s2v-01"].video_caps_for_model
        hailuo = ENDPOINT_REGISTRY["minimax-hailuo-v1"].video_caps_for_model
        fast = ENDPOINT_REGISTRY["minimax-hailuo-v1-fast"].video_caps_for_model
        assert s2v is not None and s2v("S2V-01").max_reference_images == 1
        assert hailuo is not None and hailuo("MiniMax-Hailuo-2.3").first_frame is True
        # Fast 与 2.3 只差这一位：首帧必需 ⇒ text_to_video 推导为 False。
        assert hailuo("MiniMax-Hailuo-2.3").text_to_video is True
        assert fast is not None and fast("MiniMax-Hailuo-2.3-Fast").text_to_video is False

    def test_kling_caps_fn_reads_per_model_limit_without_client(self):
        """kling-video 的 caps_fn 是纯函数：v3-omni / video-o1 多图主体 R2V max_ref=4，turbo 等其余档
        走首尾帧无参考（max_ref=0），未登记 model（bearer 透传）回落保守默认，resolver 据此解析而无需
        构造 backend / api_key。"""
        caps_fn = ENDPOINT_REGISTRY["kling-video"].video_caps_for_model
        assert caps_fn is not None
        omni = caps_fn("kling-v3-omni")
        assert omni.max_reference_images > 0
        assert omni.max_reference_images == 4
        o1 = caps_fn("kling-video-o1")
        assert o1.max_reference_images > 0
        assert o1.max_reference_images == 4
        turbo = caps_fn("kling-v2-5-turbo")
        assert turbo.first_frame is True
        assert turbo.max_reference_images == 0
        assert turbo.max_reference_images == 0
        # 中转 model_id 带厂商前缀（仓库既有约定 / 与 :）+ 非规范大小写：归一化后仍能精确命中已登记档
        for prefixed_id in ("vendor/Kling-V3-Omni", "provider:kling-v3-omni"):
            prefixed = caps_fn(prefixed_id)
            assert prefixed.max_reference_images > 0
            assert prefixed.max_reference_images == 4
        # 未登记 model（未来版本 kling-v4 / 归一化后仍不匹配的中转自定义 id）→ 保守默认，不按子串猜能力
        for unknown_id in ("kling-v4", "vendor/some-unknown-model"):
            unknown = caps_fn(unknown_id)
            assert unknown.max_reference_images == 0
            assert unknown.max_reference_images == 0

    def test_negative_int_cap_rejected_at_validation(self):
        """import 期不变式拒绝负数 int cap：下游 references[:-1] 会误丢最后一张而非裁成 0 张。"""
        import dataclasses

        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["openai-video"], video_max_reference_images=-1, video_caps_for_model=None
        )
        with pytest.raises(ValueError, match="negative video_max_reference_images"):
            validate_video_caps_declaration(bad)

    def test_non_callable_caps_fn_rejected_at_validation(self):
        """import 期不变式拒绝非 callable 的 video_caps_for_model：否则误填字符串/整数会放行到
        request 期才在 resolver `caps_fn(model_id)` 处炸，违背 fail-fast 初衷。"""
        import dataclasses

        # 非 callable 真值（字符串）冒充 caps_fn；同时清掉 int cap 避免先撞 XOR 校验
        bad = dataclasses.replace(
            ENDPOINT_REGISTRY["ark-seedance"],
            video_max_reference_images=None,
            video_caps_for_model="not-callable",
        )
        with pytest.raises(ValueError, match="non-callable video_caps_for_model"):
            validate_video_caps_declaration(bad)

    def test_non_video_endpoint_declaring_reference_audio_rejected(self):
        """import 期不变式：reference_audio_capable 只对 video 类有意义，非 video 类声明即 misconfig。"""
        import dataclasses

        bad = dataclasses.replace(ENDPOINT_REGISTRY["openai-chat"], reference_audio_capable=True)
        with pytest.raises(ValueError, match="must not declare reference_audio_capable"):
            validate_video_caps_declaration(bad)

    def test_audio_capable_endpoints_match_backends_that_send_audio(self):
        """运输声明与 backend 实现同步：声明 True 的 endpoint 必须真的组装参考音频。"""
        audio_capable = {k for k, s in ENDPOINT_REGISTRY.items() if s.reference_audio_capable}
        assert audio_capable == {"ark-seedance", "dashscope-async-video", "minimax-h3"}

    def test_audio_endpoint_spec(self):
        spec = ENDPOINT_REGISTRY["openai-tts"]
        assert spec.media_type == "audio"
        assert spec.family == "openai"
        assert spec.request_path_template == "/v1/audio/speech"
        # 非 video/image endpoint：不声明 video caps / image capabilities
        assert spec.video_max_reference_images is None
        assert spec.video_caps_for_model is None
        assert spec.image_capabilities is None

    def test_media_type_groups(self):
        text_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "text"}
        image_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "image"}
        video_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "video"}
        audio_keys = {s.key for s in ENDPOINT_REGISTRY.values() if s.media_type == "audio"}
        assert audio_keys == {"openai-tts"}
        assert text_keys == {"openai-chat", "gemini-generate"}
        assert image_keys == {
            "openai-images",
            "openai-images-generations",
            "openai-images-edits",
            "gemini-image",
            "dashscope-image",
            "minimax-image",
            "kling-image",
        }
        assert video_keys == {
            "openai-video",
            "newapi-video",
            "v2-video-generations",
            "ark-seedance",
            "vidu-video",
            "dashscope-async-video",
            "minimax-hailuo-v1",
            "minimax-hailuo-v1-fast",
            "minimax-s2v-01",
            "minimax-h3",
            "kling-video",
        }


class TestHelpers:
    def test_get_endpoint_spec(self):
        spec = get_endpoint_spec("openai-chat")
        assert spec.media_type == "text"

    def test_get_endpoint_spec_unknown_raises(self):
        with pytest.raises(ValueError, match="unknown endpoint"):
            get_endpoint_spec("anthropic-messages")

    def test_endpoint_to_media_type(self):
        assert endpoint_to_media_type("newapi-video") == "video"
        assert endpoint_to_media_type("gemini-image") == "image"

    def test_endpoint_to_media_type_unknown_raises(self):
        with pytest.raises(ValueError):
            endpoint_to_media_type("nope")

    def test_list_endpoints_by_media_type(self):
        text = list_endpoints_by_media_type("text")
        assert {s.key for s in text} == {"openai-chat", "gemini-generate"}


class TestInferEndpoint:
    @pytest.mark.parametrize(
        "model_id,discovery_format,expected",
        [
            # ── content-first 纠偏（中转站普遍 discovery_format="openai" 却夹带原生 id）──
            ("gemini-2.5-flash", "openai", "gemini-generate"),  # 不再被错推到 openai-chat
            ("gemini-2.5-flash", "google", "gemini-generate"),
            ("imagen-4", "openai", "gemini-image"),  # imagen 一律 gemini-image
            ("imagen-4", "google", "gemini-image"),
            ("gemini-imagen-3", "openai", "gemini-image"),  # imagen 优先于 gemini 文本
            # gemini 原生图像模型也按内容纠偏到 gemini-image（不被错推到 openai-images）
            ("gemini-2.5-flash-image", "openai", "gemini-image"),
            ("gemini-2.5-flash-image", "google", "gemini-image"),
            ("gemini-2.0-flash-exp-image-generation", "openai", "gemini-image"),
            ("gemini-3-pro-image-preview", "openai", "gemini-image"),
            # ── 新视频分支路由 ──
            ("seedance-1.0", "openai", "ark-seedance"),
            ("doubao-seedance-2-0", "openai", "ark-seedance"),
            ("viduq3", "openai", "vidu-video"),
            ("viduq3-mix", "openai", "vidu-video"),
            ("viduq3-pro", "openai", "vidu-video"),
            ("viduq3-turbo", "openai", "vidu-video"),
            ("viduq3-i2v", "openai", "vidu-video"),
            ("proxy/viduq3-turbo", "openai", "vidu-video"),
            # ── 阿里百炼 wan2.x / wan3.0 → dashscope-async-video（image 变体不受影响）──
            ("wan2.7-i2v", "openai", "dashscope-async-video"),
            ("wan2.7-t2v", "openai", "dashscope-async-video"),
            ("wan-2.7-i2v", "openai", "dashscope-async-video"),  # 连字符形态与点号形态匹配宽度一致
            ("wan_2.7-t2v", "openai", "dashscope-async-video"),
            ("wan3.0-video", "openai", "dashscope-async-video"),
            ("Wan3.0-Video", "openai", "dashscope-async-video"),  # 大小写不敏感
            ("proxy/wan3.0-video", "openai", "dashscope-async-video"),
            ("wan-3-turbo", "openai", "dashscope-async-video"),  # 连字符形态与点号形态匹配宽度一致
            ("wan3-turbo", "openai", "dashscope-async-video"),
            ("wan2.7-image", "openai", "openai-images"),  # image 变体不受影响
            ("wan-2.7-image", "openai", "openai-images"),  # 连字符形态的 image 变体同样落图像端点
            ("wan3.0-video-image", "openai", "openai-images"),  # 含 image 语义不受影响
            ("wan-3-turbo-image", "openai", "openai-images"),  # 连字符形态的 image 变体同样不受影响
            # image-to-video 别名含 "image" 子串但本质是视频，须留在 dashscope-async-video（同
            # kling-image2video 的"video 语义优先"原则），不能被笼统 is_image 误判成图像变体。
            ("wan-3-turbo-image-to-video", "openai", "dashscope-async-video"),
            ("wan3-image2video", "openai", "dashscope-async-video"),
            ("wan-2.7-image-to-video", "openai", "dashscope-async-video"),
            # 反向陷阱：真图像别名不保证以 "image" 结尾（版本/日期/变体后缀），不能靠"结尾是不是
            # image"反推是不是图像——只有显式 image-to-video 续接语法才算视频例外，其余含 image
            # 语义一律仍是图像。
            ("wan3.0-image-edit", "openai", "openai-images"),
            ("wan-3-turbo-image-preview", "openai", "openai-images"),
            ("wan3.0-video-image-20260801", "openai", "openai-images"),
            # 分隔符混用：下划线与连字符匹配宽度一致（WAN3_PATTERN 同时容忍二者）。
            ("wan_3_turbo", "openai", "dashscope-async-video"),
            ("wan_3.0-image", "openai", "openai-images"),
            ("WAN_3_TURBO_IMAGE_TO_VIDEO", "openai", "dashscope-async-video"),
            # 标识符边界：含 "wan3"/"wan2" 子串但并非该家族的型号名不得被误判——WAN2_PATTERN /
            # WAN3_PATTERN 两侧要求非字母数字边界，裸 "wan" 仍命中 _VIDEO_PATTERN 落回通用视频分支。
            ("swan3", "openai", "openai-video"),
            ("vendorwan3", "openai", "openai-video"),
            ("wan30", "openai", "openai-video"),
            ("swan2", "openai", "openai-video"),
            ("vendorwan2", "openai", "openai-video"),
            ("wan20", "openai", "openai-video"),
            # 同一边界陷阱、但字母粘连前缀后接完整点号形态 + 版本号 + 模态后缀：_WAN_DOT_FORM_PATTERN
            # 若不做左侧边界校验，"swan2.7-r2v" 去掉首字符即与 "wan2.7-r2v" 字面相同，会被误判命中。
            ("swan2.7-r2v", "openai", "openai-video"),
            ("vendorwan2.7-t2v", "openai", "openai-video"),
            # 万相 2.x 小版本边界：WAN2_PATTERN 只认 2.7，其余 2.x（2.1/2.2 等）的连字符/下划线
            # 形态不落原生端点——本后端固定请求的 video-generation/video-synthesis 端点与这些
            # 小版本的实际协议不符（见 dashscope.py WAN2_PATTERN 处的说明）。点号形态另受独立的
            # 字面量判定约束，不受 WAN2_PATTERN 的版本锚定限制。
            ("wan-2.1-kf2v", "openai", "openai-video"),  # 连字符 + 非 2.7 → 通用端点
            ("wan_2.2-t2v", "openai", "openai-video"),  # 下划线 + 非 2.7 → 通用端点
            ("wan2.1-kf2v", "openai", "dashscope-async-video"),  # 点号形态走字面量判定，非本正则
            # 2.7 家族内 videoedit 模态：命中家族正则但 DashScopeVideoBackend 未实现其请求构造，
            # 排除出原生路由（见 _WAN_VIDEOEDIT_PATTERN 处的说明）。
            ("wan-2.7-videoedit", "openai", "openai-video"),
            ("wan_2.7-videoedit", "openai", "openai-video"),
            ("wan2.7-videoedit", "openai", "openai-video"),
            # happyhorse 同一边界要求：与 DashScopeVideoBackend._profile_for_model 的兜底子串匹配
            # 对同一 key 保持同等边界，否则会出现"路由到本后端却拿不到对应能力档"的矛盾。
            ("myhappyhorse-1.0-r2v", "openai", "openai-chat"),
            ("happyhorse-1.0-r2v", "openai", "dashscope-async-video"),
            # ── 向后兼容（行为不变）──
            ("gpt-4o", "openai", "openai-chat"),
            ("claude-sonnet-4.5", "openai", "openai-chat"),
            ("dall-e-3", "openai", "openai-images"),
            ("gpt-image-1", "openai", "openai-images"),
            ("flux-pro", "openai", "openai-images"),
            ("sora-2", "openai", "openai-video"),
            ("SORA-2", "openai", "openai-video"),
            ("veo-3", "openai", "openai-video"),
            ("veo-3", "google", "openai-video"),  # 非 seedance/viduq3/minimax 视频 → openai-video
            # ── MiniMax 原生 token 二级路由 ──
            ("MiniMax-Hailuo-2.3", "openai", "minimax-hailuo-v1"),
            ("MiniMax-Hailuo-2.3-Fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3-fast", "openai", "minimax-hailuo-v1-fast"),
            ("minimax-hailuo-2.3", "openai", "minimax-hailuo-v1"),
            (
                "hailuo-02",
                "openai",
                "minimax-hailuo-v1",
            ),
            ("S2V-01", "openai", "minimax-s2v-01"),
            ("minimax-s2v-01", "openai", "minimax-s2v-01"),
            ("MiniMax-H3", "openai", "minimax-h3"),
            ("minimax-h3", "openai", "minimax-h3"),
            ("h3", "openai", "openai-chat"),  # 裸 "h3" 不应匹配——防止退化成过于宽松的子串
            ("other-vendor-h3", "openai", "openai-chat"),  # 其它厂商恰好含 h3 子串同样不应误路由
            ("image-01", "openai", "minimax-image"),  # image-01 含 "image" 否则会被推到通用图像家族
            ("minimax/image-01", "openai", "minimax-image"),
            ("S2V-01", "google", "minimax-s2v-01"),  # minimax 路由不分 discovery_format
            # ── Kling 原生中转二级路由（视频 family 含 kling，须收敛到 kling-video 而非 openai-video）──
            ("kling-v2-5-turbo", "openai", "kling-video"),
            ("kling-v2", "openai", "kling-video"),  # 前 kling endpoint 时代默认 openai-video
            ("kling-v3", "openai", "kling-video"),
            ("kling-v2-6", "openai", "kling-video"),
            ("proxy/kling-v2-5-turbo", "openai", "kling-video"),
            ("KLING-V3", "openai", "kling-video"),  # 大小写不敏感
            ("kling-v3-omni", "openai", "kling-video"),  # 图像/视频同名歧义 → 默认归视频
            # 含 image 语义的可灵图像 → kling-image（先于通用图像家族，不被推到 openai-images）
            ("kling-image-o1", "openai", "kling-image"),
            ("kling-v3-omni-image", "openai", "kling-image"),
            ("proxy/kling-image-o1", "openai", "kling-image"),
            ("kling-image-o1", "google", "kling-image"),  # kling 路由不分 discovery_format
            # image-to-video 含 image 语义但本质是视频 → video 优先于 image，归 kling-video
            ("kling-image2video", "openai", "kling-video"),
            ("kling-img2video", "openai", "kling-video"),
            ("proxy/kling-image2video", "openai", "kling-video"),
            ("seedream-3.0", "openai", "openai-images"),
            ("jimeng-3.0", "openai", "openai-images"),
            ("jimeng-video-3.0", "openai", "openai-video"),
            ("jimengvideo-3.0", "openai", "openai-video"),
            # ── 纯文本 MiniMax model 落到文本端点（不被裸 minimax 误推到视频）──
            ("MiniMax-M2.7", "openai", "openai-chat"),
            ("minimax-abab-6.5-chat", "openai", "openai-chat"),
            ("MiniMax-M2.7", "google", "gemini-generate"),  # discovery_format=google → gemini-generate
            # viduq1/viduq2 是 vidu 早期图像版本 → 维持 image 推断不变
            ("viduq1", "openai", "openai-images"),
            ("viduq1-classic", "openai", "openai-images"),
            ("my-proxy/viduq1", "openai", "openai-images"),
            ("viduq2", "openai", "openai-images"),
            ("viduq2-pro", "openai", "openai-images"),
            ("viduq2-turbo", "openai", "openai-images"),
            ("provider:viduq2-turbo", "openai", "openai-images"),
            ("vidu2", "openai", "openai-video"),
            ("vidu2.0", "openai", "openai-video"),
            ("provider:vidu2.0", "openai", "openai-video"),
            # ── audio（TTS）识别：precedence 在 text 默认之前 ──
            ("tts-1", "openai", "openai-tts"),
            ("tts-1-hd", "openai", "openai-tts"),
            ("gpt-4o-mini-tts", "openai", "openai-tts"),
            ("vidu-tts", "openai", "openai-tts"),  # tts 尾缀优先于 text 默认
            ("speech-1.5", "openai", "openai-tts"),  # Fish Audio 风格 id
            ("cosyvoice-v2", "openai", "openai-tts"),
            # audio endpoint 仅 OpenAI 兼容一条，google 发现格式同样归 openai-tts
            ("tts-1", "google", "openai-tts"),
            # 不应误伤：含 audio 字样的 chat 模型、ASR（语音转文字）、视频/图像家族仍按原分支
            ("gpt-4o-audio-preview", "openai", "openai-chat"),
            ("whisper-1", "openai", "openai-chat"),
            ("speech-to-text-1", "openai", "openai-chat"),
            ("transcribe-speech-1", "openai", "openai-chat"),
        ],
    )
    def test_infer(self, model_id, discovery_format, expected):
        assert infer_endpoint(model_id, discovery_format) == expected

    @pytest.mark.parametrize(
        "model_id,discovery_format",
        [
            ("seedance-1.0", "openai"),
            ("viduq3-turbo", "openai"),
            ("kling-v2", "openai"),
            ("some-v2-model", "openai"),
            ("gpt-4o", "openai"),
        ],
    )
    def test_v2_never_auto_inferred(self, model_id, discovery_format):
        """v2-video-generations 命名碎片化无法可靠识别，永不自动推断，留用户手选。"""
        assert infer_endpoint(model_id, discovery_format) != "v2-video-generations"


def test_image_endpoint_registry_entries():
    from lib.custom_provider.endpoints import ENDPOINT_KEYS_BY_MEDIA_TYPE

    image_keys = set(ENDPOINT_KEYS_BY_MEDIA_TYPE["image"])
    assert image_keys == {
        "openai-images",
        "openai-images-generations",
        "openai-images-edits",
        "gemini-image",
        "dashscope-image",
        "minimax-image",
        "kling-image",
    }


def test_split_endpoints_have_single_capability():
    from lib.custom_provider.endpoints import endpoint_to_image_capabilities
    from lib.image_backends import ImageCapability

    assert endpoint_to_image_capabilities("openai-images-generations") == frozenset({ImageCapability.TEXT_TO_IMAGE})
    assert endpoint_to_image_capabilities("openai-images-edits") == frozenset({ImageCapability.IMAGE_TO_IMAGE})


def test_existing_image_endpoints_have_full_capabilities():
    """EndpointSpec 新增 image_capabilities 字段；已存在的 image entry 默认填两个能力。"""
    from lib.custom_provider.endpoints import (
        ENDPOINT_REGISTRY,
        endpoint_spec_to_dict,
        endpoint_to_image_capabilities,
    )
    from lib.image_backends import ImageCapability

    full = frozenset({ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE})
    assert ENDPOINT_REGISTRY["openai-images"].image_capabilities == full
    assert ENDPOINT_REGISTRY["gemini-image"].image_capabilities == full
    assert ENDPOINT_REGISTRY["openai-chat"].image_capabilities is None
    assert endpoint_to_image_capabilities("openai-images") == full

    with pytest.raises(ValueError):
        endpoint_to_image_capabilities("openai-chat")

    # Verify endpoint_spec_to_dict serializes capabilities to sorted list[str]
    serialized = endpoint_spec_to_dict(ENDPOINT_REGISTRY["openai-images"])
    assert serialized["image_capabilities"] == ["image_to_image", "text_to_image"]
