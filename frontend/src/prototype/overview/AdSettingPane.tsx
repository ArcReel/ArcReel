// PROTOTYPE — 广告项目视频页「故事设定」tab 的内容（#2981），不合并。沿用概览原型的设定区块。

import type { ProjectData } from "@/types";
import { StorySetting } from "./sections";

export function AdSettingPane({ data }: { data: ProjectData }) {
  return (
    <div className="@container relative h-full overflow-y-auto [scrollbar-gutter:stable]">
      <div className="max-w-[calc(760px+4rem)] px-6 py-6 xl:px-8">
        <StorySetting data={data} fields="seamless" analyzing={false} emptyProject={false} onUploadSource={() => {}} />
      </div>
    </div>
  );
}
