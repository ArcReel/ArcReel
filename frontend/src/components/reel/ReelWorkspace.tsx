import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { Film, Loader2, WandSparkles } from "lucide-react";
import { API } from "@/api";
import { Button, buttonVariants } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { PageShell } from "@/components/shared/page-shell/PageShell";
import { PageHeader } from "@/components/shared/page-shell/PageHeader";
import { useLeaveGuard } from "@/components/shared/edit-unit/LeaveGuard";
import { AgentQuestionnaire } from "@/components/copilot/AgentQuestionnaire";
import { useAssistantSession } from "@/hooks/useAssistantSession";
import { useTaskRefresh } from "@/hooks/useTaskRefresh";
import { useProjectEventsSSE } from "@/hooks/useProjectEventsSSE";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isOccupyingStatus, useTasksStore } from "@/stores/tasks-store";
import type { EditTimelineReadout, EditTimelineSummary } from "@/types/edit-timeline";
import type { FinalCutStatus } from "@/types/edit-render";
import { errMsg, voidCall } from "@/utils/async";
import { formatClock, pickDefaultTimeline } from "@/components/canvas/edit/timeline-view";
import { getScriptItems, getScriptItemId } from "@/utils/script-shape";
import { revisionRequest } from "./revision-request";

export function ReelWorkspace({ projectName, episode }: { projectName: string; episode: number }) {
  const { t } = useTranslation("dashboard");
  const project = useProjectsStore((s) => s.currentProjectData);
  const scripts = useProjectsStore((s) => s.currentScripts);
  const fingerprints = useProjectsStore((s) => s.assetFingerprints);
  const state = useAssistantStore();
  const { sendMessage, answerQuestion, interrupt } = useAssistantSession(projectName);
  useTaskRefresh(projectName);
  useProjectEventsSSE(projectName, false);
  const [timelines, setTimelines] = useState<EditTimelineSummary[]>([]);
  const [timelineId, setTimelineId] = useState<string | null>(null);
  const [media, setMedia] = useState<{ timeline: EditTimelineReadout; cut: FinalCutStatus } | null>(null);
  const [unitId, setUnitId] = useState<string | null>(null);
  const [rawUnitId, setRawUnitId] = useState<string | null>(null);
  const [instruction, setInstruction] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const player = useRef<HTMLVideoElement>(null);
  const requestInFlight = useRef(false);
  const pendingPlaybackStart = useRef<number | null>(null);
  const selectedTimeline = timelineId ?? pickDefaultTimeline(timelines);
  const running = state.sessionStatus === "running";
  const busy = state.sending || Boolean(state.pendingQuestion);
  const meta = project?.episodes?.find((entry) => entry.episode === episode);
  const script = meta?.script_file ? scripts[meta.script_file.replace(/^scripts\//, "")] : null;
  const rendering = useTasksStore((s) => s.tasks.some((task) =>
    task.project_name === projectName && isOccupyingStatus(task.status) &&
    task.script_file?.replace(/^scripts\//, "") === meta?.script_file?.replace(/^scripts\//, "")
  ));
  const narration = project?.narration_delivery === "use_tts" ? "with_narration" : "without_narration";

  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      try {
        const result = await API.listEditTimelines(projectName, episode, { signal: controller.signal });
        if (!controller.signal.aborted) { setTimelines(result.timelines); setError(null); }
      } catch (cause) { if (!controller.signal.aborted) setError(errMsg(cause)); }
    };
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [projectName, episode, script]);

  useEffect(() => {
    const controller = new AbortController();
    if (!selectedTimeline) return;
    const load = async () => {
      try {
        const [timeline, cut] = await Promise.all([
          API.getEditTimeline(projectName, selectedTimeline, { signal: controller.signal }),
          API.getFinalCutStatus(projectName, selectedTimeline, { narration, subtitles: "no_subtitles", signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) { setMedia({ timeline, cut }); setError(null); }
      } catch (cause) { if (!controller.signal.aborted) setError(errMsg(cause)); }
    };
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => { controller.abort(); clearInterval(timer); };
  }, [projectName, selectedTimeline, narration]);

  const current = media?.timeline.timeline.id === selectedTimeline ? media : null;
  const stopped = ["completed", "error", "interrupted"].includes(state.sessionStatus ?? "");
  const progressLabel = running ? t("reel_working") : rendering ? t("work_status_running")
    : current?.cut.download_url ? t("reel_review_cut")
    : stopped ? t("usage_attention_title") : t("todo_status_pending");
  const selectedClip = current?.timeline.clips.find((clip) => clip.unit_id === unitId);
  const contentMode = script?.content_mode ?? "drama";
  const renderedShots = getScriptItems(script, contentMode).flatMap((item, index) => {
    const assets = item.generated_assets;
    if (!assets?.video_clip) return [];
    return [{ id: getScriptItemId(item, contentMode), number: index + 1, path: assets.video_clip, thumbnail: assets.video_thumbnail ?? assets.storyboard_image }];
  });
  const selectedTake = renderedShots.find((shot) => shot.id === rawUnitId);
  const playbackUrl = selectedTake ? API.getFileUrl(projectName, selectedTake.path, fingerprints[selectedTake.path] ?? null) : current?.cut.download_url;
  const playAt = (start: number) => {
    if (player.current) { player.current.currentTime = start; void player.current.play().catch(() => undefined); }
  };
  const showCut = () => { setRawUnitId(null); setUnitId(null); pendingPlaybackStart.current = null; if (player.current) player.current.currentTime = 0; };
  const submit = useCallback(async () => {
    if (!instruction.trim() || busy || requestInFlight.current) return false;
    requestInFlight.current = true;
    try {
      const accepted = await sendMessage(revisionRequest(episode, selectedTimeline, unitId, instruction, running));
      if (accepted) { setInstruction(""); setSubmitted(true); }
      return accepted;
    } finally { requestInFlight.current = false; }
  }, [instruction, busy, sendMessage, episode, selectedTimeline, unitId, running]);
  useLeaveGuard({ dirty: Boolean(instruction.trim()), saving: state.sending, save: submit, discard: () => setInstruction("") });
  const latestNote = [...state.entries].reverse().find((entry) => entry.type === "assistant" && Array.isArray(entry.content) && entry.content.some((block) => block.type === "text"));
  const note = Array.isArray(latestNote?.content) ? latestNote.content.flatMap((block) => block.type === "text" ? [block.text] : []).join("\n") : null;

  return (
    <PageShell tier="full" header={<PageHeader title={project?.title ?? t("reel_title")} actions={
      <Link href={`/episodes/${episode}`} className={buttonVariants({ variant: "ghost", size: "sm" })}>{t("reel_advanced")}</Link>
    } />}>
      <div className="@container mx-auto flex w-full max-w-6xl flex-col gap-8">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="space-y-2"><p className="text-sm text-muted-foreground">{t("reel_eyebrow")}</p><h2 className="text-2xl font-semibold">{meta?.title ?? t("reel_title")}</h2><p className="text-sm text-muted-foreground">{t("reel_intro")}</p></div>
          <div className="space-y-2">
            <span role="status" className="flex items-center gap-2 text-sm">{(running || rendering) && <Loader2 aria-hidden className="size-4 animate-spin" />}{progressLabel}</span>
            {meta?.videos && meta.videos.total > 0 && <p className="text-sm text-muted-foreground">{t("episode_available_videos_hint", { count: meta.videos.available, total: meta.videos.total })}</p>}
          </div>
        </div>
        {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
        <div className="overflow-hidden rounded-xl border border-border bg-black">
          {playbackUrl ? (
            <video key={playbackUrl} ref={player} src={playbackUrl} controls playsInline preload="metadata" className="aspect-video w-full" aria-label={t("reel_player")}
              onLoadedMetadata={() => { if (pendingPlaybackStart.current !== null) { const start = pendingPlaybackStart.current; pendingPlaybackStart.current = null; playAt(start); } }}
              onTimeUpdate={() => { if (!selectedTake && selectedClip && player.current && player.current.currentTime >= selectedClip.start + selectedClip.duration) player.current.pause(); }}>
              <track kind="captions" />
            </video>
          ) : <div className="flex aspect-video flex-col items-center justify-center gap-4 px-6 text-center text-white"><Film aria-hidden className="size-10" /><p>{t("reel_empty")}</p><p className="max-w-lg text-sm text-white/70">{t("reel_empty_hint")}</p></div>}
        </div>
        {renderedShots.length > 0 && <section className="min-w-0 space-y-3" aria-label={t("reel_rendered_shots")}>
          <div className="flex flex-wrap items-center justify-between gap-3"><h3 className="font-medium">{t("reel_rendered_shots")} · {renderedShots.length}</h3><Button size="sm" variant={selectedTake ? "outline" : "secondary"} disabled={!current?.cut.download_url} onClick={showCut}>{t("reel_stitched_cut")}</Button></div>
          <p className="text-sm text-muted-foreground">{t("reel_raw_hint")}</p>
          <div className="relative flex gap-3 overflow-x-auto pb-2">{renderedShots.map((shot) => <button key={shot.id} type="button" aria-label={t("reel_preview_shot", { number: shot.number })} aria-pressed={rawUnitId === shot.id} className={`w-32 shrink-0 overflow-hidden rounded-lg border text-left text-sm focus-visible:outline-2 focus-visible:outline-ring ${rawUnitId === shot.id ? "border-primary bg-primary/10" : "border-border bg-card"}`} onClick={() => { setUnitId(shot.id); setRawUnitId(shot.id); if (rawUnitId === shot.id) playAt(0); else pendingPlaybackStart.current = 0; }}>
            {shot.thumbnail ? <img src={API.getFileUrl(projectName, shot.thumbnail, fingerprints[shot.thumbnail] ?? null, { width: 320 })} alt="" loading="lazy" className="aspect-video w-full object-cover" /> : <div className="flex aspect-video items-center justify-center bg-muted"><Film aria-hidden className="size-6" /></div>}
            <span className="block px-3 py-2">{t("reel_shot", { number: shot.number })}</span>
          </button>)}</div>
          {selectedTake && <p role="status" className="text-sm text-muted-foreground">{t("reel_raw_playing", { number: selectedTake.number })}</p>}
        </section>}
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex flex-wrap gap-2">{timelines.map((timeline) => <Button key={timeline.id} size="sm" variant={timeline.id === selectedTimeline ? "default" : "outline"} onClick={() => { setTimelineId(timeline.id); showCut(); }}>{timeline.name}</Button>)}</div>
          <p className="text-sm text-muted-foreground">{current ? formatClock(current.timeline.duration) : t("reel_target", { seconds: project?.episode_target_duration ?? 180 })}</p>
        </div>
        {current?.cut.status === "stale" && <p className="text-sm text-muted-foreground">{t("reel_stale")}</p>}
        <div className="grid items-start gap-8 @lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
          <section className="space-y-4" aria-label={t("reel_change")}>
            <div className="flex flex-wrap gap-2"><Button variant={unitId === null ? "secondary" : "outline"} size="sm" onClick={showCut}>{t("reel_whole")}</Button>
              {current?.timeline.clips.map((clip, index) => <Button key={clip.id} size="sm" variant={clip.unit_id === unitId ? "secondary" : "outline"} onClick={() => { setUnitId(clip.unit_id); setRawUnitId(null); if (selectedTake) pendingPlaybackStart.current = clip.start; else playAt(clip.start); }}>{t("reel_shot", { number: index + 1 })}</Button>)}
            </div>
            <label htmlFor="reel-change" className="block text-lg font-medium">{t("reel_change")}</label>
            <Textarea id="reel-change" value={instruction} onChange={(event) => { setInstruction(event.target.value); setSubmitted(false); }} placeholder={t("reel_placeholder")} rows={5} />
            <p className="text-sm text-muted-foreground">{t("reel_preserve")}</p>
            {state.error && <p role="alert" className="text-sm text-destructive">{state.error}</p>}
            {running && <p className="text-sm text-muted-foreground">{t("reel_queue_hint")}</p>}
            <div className="flex flex-wrap gap-3"><Button disabled={busy || !instruction.trim()} onClick={() => voidCall(submit())}><WandSparkles aria-hidden data-icon="inline-start" />{state.sending ? t("reel_sending") : running ? t("reel_queue") : t("reel_revise")}</Button>{running && <Button variant="outline" onClick={() => voidCall(interrupt())}>{t("reel_stop")}</Button>}</div>
            {submitted && <p role="status" className="text-sm text-muted-foreground">{t("reel_submitted")}</p>}
            {state.pendingQuestion && <AgentQuestionnaire pendingQuestion={state.pendingQuestion} error={state.error} answering={state.answeringQuestion} onSubmit={(id, answers) => voidCall(answerQuestion(id, answers))} />}
          </section>
          <section className="space-y-3 rounded-xl border border-border p-6" aria-label={t("reel_progress")}><h3 className="font-medium">{t("reel_progress")}</h3><p className="text-sm text-muted-foreground">{t("reel_honest")}</p>{note && <p className="whitespace-pre-wrap wrap-break-word text-sm leading-relaxed">{note}</p>}</section>
        </div>
      </div>
    </PageShell>
  );
}
