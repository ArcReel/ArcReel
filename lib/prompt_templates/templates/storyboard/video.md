---
id: storyboard/video
category: video
title: 分镜视频
description: 分镜生视频的完整提示词；负向约束排除 BGM、文字字幕、水印与 Logo，发声内容由编排层按能力门控投影。Avoid 以块级片段引用，保证预览转为纯文本后重复渲染不叠加。
applies_to: {}
slots:
  body: 已序列化或纯文本的视频正文，含按门控派生的发声声明
protected: false
---
{{ body }}

{{ partial("storyboard/video/avoid") }}
