---
id: storyboard/video
category: video
title: 分镜视频
description: 分镜生视频的完整提示词，由视频正文与负向约束组成。
stage: storyboard_video
invoked_by:
  kind: generation_task
  name: video
applies_to: {}
slots:
  body: 已序列化或纯文本的视频正文，含按门控派生的发声声明
protected: false
idempotent: true
---
{{ body }}

{{ partial("storyboard/video/avoid") }}
