---
status: accepted
---

# 像素尺寸优先满足项目比例

项目的 `aspect_ratio` 决定请求的比例，分辨率档位决定像素规模。用户给出 `1920x1080` 一类分辨率时只取其短边，项目为 9:16 仍按 9:16 算宽高。这样可避免供应商尺寸表或用户输入的宽高无意覆盖项目比例。

接受任意像素尺寸的通道共用 [尺寸计算](../../packages/arcreel-market-core/src/arcreel_market_core/aspect_size.py)，再施加各自的像素约束。需要像素尺寸控制比例且未配置分辨率时，通用算法以 720 为目标短边。只有固定尺寸档位的通道从合法值中选最接近项目比例的一档。协议分别接收比例与可省略的分辨率时，按 [ADR 0019](0019-resolution-none-means-omit-param.md) 交由上游处理未设置的分辨率。ComfyUI 没有尺寸节点绑定时沿用 workflow 字面值，见 [ADR 0082](0082-node-bindings-are-the-only-truth-of-comfyui-endpoint.md)。
