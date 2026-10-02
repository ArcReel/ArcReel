// PROTOTYPE — 剧集页原型（#2974）：分镜详情的栏位布局。
// editor 轴决定宽窗口的余量给谁：提示词（现状）/ 媒体栏 / 第四栏；
// sizing 轴决定是按容器宽度切固定布局，还是各栏可拖拽调宽。
// 容器窄于 980 时三种取向都退回现状的「中栏 + 媒体栏，左栏进抽屉」。

import { useCallback, useRef, useState, type ReactNode } from "react";
import { ResponsiveDetailGrid } from "@/components/canvas/timeline/ResponsiveDetailGrid";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useEpisodeProto } from "./store";

interface Props {
  left: ReactNode;
  mid: ReactNode;
  /** 现状的右栏：分镜图 + 尾帧 + 视频 + 配音 */
  right: ReactNode;
  /** 拆开的媒体块，供「媒体栏并排」和「第四栏」使用 */
  storyboard: ReactNode;
  video: ReactNode;
  revealRightKey?: string | null;
  aspectRatio: "9:16" | "16:9";
}

const col = "min-h-0 h-full overflow-y-auto overscroll-contain";
const MID_MAX = 560;

function useWidth() {
  const [width, setWidth] = useState(0);
  const ro = useRef<ResizeObserver | null>(null);
  const ref = useCallback((el: HTMLDivElement | null) => {
    ro.current?.disconnect();
    if (!el) return;
    setWidth(el.getBoundingClientRect().width);
    ro.current = new ResizeObserver((entries) => {
      for (const e of entries) setWidth(e.contentRect.width);
    });
    ro.current.observe(el);
  }, []);
  return [width, ref] as const;
}

/** 媒体栏：自身宽度够两张卡时分镜图与视频并排，否则上下排。 */
function MediaColumn({ storyboard, video, aspectRatio }: { storyboard: ReactNode; video: ReactNode; aspectRatio: "9:16" | "16:9" }) {
  // 竖屏两张卡并排只需约 440px，横屏要 720px 才不至于缩得太小
  const sideBySide = aspectRatio === "9:16" ? "@min-[440px]/media:grid-cols-2" : "@min-[720px]/media:grid-cols-2";
  return (
    <div className={`${col} @container/media px-[18px] pb-7 pt-3.5`}>
      <div className={`grid items-start gap-4 ${sideBySide}`}>
        <div className="flex min-w-0 flex-col gap-4">{storyboard}</div>
        <div className="flex min-w-0 flex-col gap-4">{video}</div>
      </div>
    </div>
  );
}

export function ProtoDetailGrid({ left, mid, right, storyboard, video, revealRightKey, aspectRatio }: Props) {
  const { axes } = useEpisodeProto();
  const [width, ref] = useWidth();
  const wide = width === 0 || width >= 980;
  const veryWide = width >= 1280;

  let body: ReactNode;
  if (!wide || (axes.editor === "current" && axes.sizing === "tiers")) {
    body = <ResponsiveDetailGrid left={left} mid={mid} right={right} revealRightKey={revealRightKey} />;
  } else if (axes.sizing === "tiers") {
    if (axes.editor === "fourCol" && veryWide) {
      body = (
        <div
          className="grid h-full min-h-0 divide-x divide-[var(--color-hairline-soft)]"
          style={{ gridTemplateColumns: `220px minmax(360px, ${MID_MAX}px) minmax(260px, 1fr) minmax(260px, 1fr)` }}
        >
          <div className={col}>{left}</div>
          <div className={col}>{mid}</div>
          <div className={`${col} px-[18px] pb-7 pt-3.5`}>
            <div className="flex flex-col gap-4">{storyboard}</div>
          </div>
          <div className={`${col} px-[18px] pb-7 pt-3.5`}>
            <div className="flex flex-col gap-4">{video}</div>
          </div>
        </div>
      );
    } else {
      body = (
        <div
          className="grid h-full min-h-0 divide-x divide-[var(--color-hairline-soft)]"
          style={{ gridTemplateColumns: `220px minmax(360px, ${MID_MAX}px) minmax(320px, 1fr)` }}
        >
          <div className={col}>{left}</div>
          <div className={col}>{mid}</div>
          <MediaColumn storyboard={storyboard} video={video} aspectRatio={aspectRatio} />
        </div>
      );
    }
  } else {
    // 可拖拽调宽：像素上下限，双击手柄复位由库提供
    const four = axes.editor === "fourCol" && veryWide;
    const midFlex = axes.editor === "current";
    body = (
      <ResizablePanelGroup orientation="horizontal" className="h-full min-h-0">
        <ResizablePanel defaultSize={240} minSize={180} maxSize={360} className="min-h-0">
          <div className={col}>{left}</div>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel
          defaultSize={midFlex ? undefined : 520}
          minSize={360}
          maxSize={midFlex ? undefined : 900}
          className="min-h-0"
        >
          <div className={col}>{mid}</div>
        </ResizablePanel>
        <ResizableHandle withHandle />
        {four ? (
          <>
            <ResizablePanel minSize={260} className="min-h-0">
              <div className={`${col} px-[18px] pb-7 pt-3.5`}>
                <div className="flex flex-col gap-4">{storyboard}</div>
              </div>
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel minSize={260} className="min-h-0">
              <div className={`${col} px-[18px] pb-7 pt-3.5`}>
                <div className="flex flex-col gap-4">{video}</div>
              </div>
            </ResizablePanel>
          </>
        ) : (
          <ResizablePanel defaultSize={midFlex ? 340 : undefined} minSize={300} className="min-h-0">
            {midFlex ? <div className={`${col}`}>{right}</div> : <MediaColumn storyboard={storyboard} video={video} aspectRatio={aspectRatio} />}
          </ResizablePanel>
        )}
      </ResizablePanelGroup>
    );
  }

  return (
    <div ref={ref} className="flex min-h-0 flex-1 flex-col overflow-hidden">
      {body}
    </div>
  );
}
