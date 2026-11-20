---
status: accepted
---

# TTS 通过生成队列执行

旁白涉及多段独立生成，浏览器内串行调用会让批次进度依赖某个标签页。Web 和 Agent 的单段或批量 TTS 都提交给生成队列，由 Worker 在音频并发池执行，任务面板读取队列状态。与文本内联调用分开，旁白因此共享媒体任务的进度和恢复入口。

当前音频通道在一次调用内返回结果。Worker 重启使在途音频任务失败，重新生成会提交新的调用。旁白的适用范围和生成粒度见 [ADR 0089](0089-single-speech-owner-per-video-unit-narration-delivery-per-project.md)，重启后的处理见 [ADR 0007](0007-orphan-tasks-not-requeued-on-restart.md)。
