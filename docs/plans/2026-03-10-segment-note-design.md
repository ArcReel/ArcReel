# 分镜备注功能设计

## 概述

在分镜卡片的文本列（第一列）下半部分添加备注区，用户可编写和查看针对每个分镜的备注。备注仅供用户参考，不参与生图/生视频。

## 数据层

在 `NarrationSegment` 和 `DramaScene` 模型中新增字段：

```python
note: Optional[str] = None
```

- 前端类型 `script.ts` 对应增加 `note?: string`
- `Optional` + `default=None` 自动兼容旧数据，无需迁移
- 生成逻辑不读取此字段，无需改动

## API 层

无需新增端点，复用现有 PATCH 接口：

- Narration：`PATCH /api/v1/projects/{name}/segments/{segment_id}` — body 含 `"note": "..."`
- Drama：`PATCH /api/v1/projects/{name}/scenes/{scene_id}` — updates 含 `"note": "..."`

## 前端 UI

在 `TextColumn` 组件中，原文/对话下方添加备注区：

- 标签 "备注"，样式与 "原文" 标签一致（普通样式，无特殊颜色）
- `textarea` 占据文本列约一半空间
- `placeholder`："添加备注..."
- 失焦时（`onBlur`）内容有变化则调用保存接口

## 涉及文件

| 文件 | 改动 |
|------|------|
| `lib/script_models.py` | `NarrationSegment` / `DramaScene` 加 `note` 字段 |
| `frontend/src/types/script.ts` | 类型加 `note?: string` |
| `frontend/src/components/canvas/timeline/SegmentCard.tsx` | `TextColumn` 中渲染备注区 |
| `frontend/src/components/canvas/StudioCanvasRouter.tsx` | 保存回调传递 note |
