---
id: storyboard/video
category: video
title: "Storyboard video"
description: "Complete storyboard-video prompt combining the video body and negative constraints."
stage: storyboard_video
invoked_by:
  kind: generation_task
  name: video
applies_to: {}
slots:
  body: "Serialized or plain-text video body, including speech declarations derived according to admission rules"
protected: false
idempotent: true
---
{{ body }}

{{ partial("storyboard/video/avoid") }}
