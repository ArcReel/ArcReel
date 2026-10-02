// PROTOTYPE — 剧集页原型（#2974）：参考视频工作台的三栏。
// refCols=resizable 时三栏可拖拽调宽（列表 240–400、预览 300–560），否则沿用现状的固定列宽。

import { Children, type ReactNode } from "react";
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable";
import { useEpisodeProto } from "./store";

export function ProtoRefColumns({
  gridCols,
  listMode,
  children,
}: {
  gridCols: string;
  listMode: "rail" | "full";
  children: ReactNode;
}) {
  const { axes } = useEpisodeProto();
  const parts = Children.toArray(children);
  if (axes.refCols !== "resizable" || parts.length < 3 || listMode === "rail") {
    return (
      <div className="grid h-full min-h-0" style={{ gridTemplateColumns: gridCols }}>
        {children}
      </div>
    );
  }
  const [list, mid, preview] = parts;
  return (
    <ResizablePanelGroup orientation="horizontal" className="h-full min-h-0">
      <ResizablePanel defaultSize={300} minSize={240} maxSize={400} className="flex min-h-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
        {list}
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel minSize={420} className="flex min-h-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
        {mid}
      </ResizablePanel>
      <ResizableHandle withHandle />
      <ResizablePanel defaultSize={360} minSize={300} maxSize={560} className="flex min-h-0 flex-col [&>*]:min-h-0 [&>*]:flex-1">
        {preview}
      </ResizablePanel>
    </ResizablePanelGroup>
  );
}
