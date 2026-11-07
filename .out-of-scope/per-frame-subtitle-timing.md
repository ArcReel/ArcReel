# Adjust individual subtitle timing inside ArcReel

ArcReel does not set individual subtitle start/end times, including Agent requests to place a subtitle at a specified frame.

## Why this is out of scope

Subtitles are mechanical character-proportional drafts without sentence-alignment claims (ADR 0089). Timelines do not store subtitles; sentence breaks/precise alignment belong to Jianying refinement (ADR 0090). Draft subtitles are independent text tracks editable individually by dragging.

Reopen if a new timeline-subtitle decision supersedes ADR 0090.

## Prior requests

- #3334 — 字幕烧入时间自定义，可以放到指定帧显示 (custom subtitle burn-in timing at specific frames)
