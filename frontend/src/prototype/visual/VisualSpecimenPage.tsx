// PROTOTYPE — 视觉方向原型（#2963）的样本页 /prototype/visual。不合并。
// 每一节对应一条视觉轴，按底栏当前的轴值渲染；数据来自真实的项目列表与任务列表。

import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, Film, MoreHorizontal, Trash2 } from "lucide-react";
import { API } from "@/api";
import type { ProjectSummary } from "@/types/project";
import type { TaskItem } from "@/types/task";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Popover, PopoverContent, PopoverDescription, PopoverHeader, PopoverTitle, PopoverTrigger } from "@/components/ui/popover";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { ActionMenu } from "@/components/ui/ActionMenu";
import { ambientGlowStyle, posterGridStyle, radioCardClass } from "@/components/ui/darkroom-tokens";
import { AXES, type AxisKey } from "./axes";
import { useVisualProto } from "./store";

// ---------------------------------------------------------------- 外框

function Section({ axis, title, children, note }: { axis?: AxisKey; title: string; children: ReactNode; note?: ReactNode }) {
  const { axes } = useVisualProto();
  return (
    <section className="border-t border-hairline-soft py-8">
      <div className="mb-5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-[17px] font-semibold text-text">{title}</h2>
        {axis && (
          <span className="text-[12px] text-text-3">
            当前：{(AXES[axis].values as Record<string, string>)[axes[axis] as string]}
          </span>
        )}
      </div>
      {children}
      {note && <div className="mt-4 max-w-[72ch] text-[13px] leading-relaxed text-text-3">{note}</div>}
    </section>
  );
}

function Label({ children }: { children: ReactNode }) {
  return <div className="mb-2 text-[12px] text-text-3">{children}</div>;
}

// ---------------------------------------------------------------- 小标题

function Eyebrow({ zh, en }: { zh: string; en?: string }) {
  const { axes } = useVisualProto();
  if (axes.eyebrow === "none") return null;
  if (axes.eyebrow === "mono") {
    return (
      <div className="mb-1.5 font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-text-3">
        {en ? `${zh} · ${en}` : zh}
      </div>
    );
  }
  return <div className="mb-1 text-[12px] font-medium text-text-3">{zh}</div>;
}

// ---------------------------------------------------------------- 运行中

function RunningIndicator({ count }: { count: number }) {
  const { axes } = useVisualProto();
  if (count === 0) return <span className="text-[12px] text-text-3">空闲</span>;
  if (axes.running === "pulse") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] text-text-2">
        <span className="h-1.5 w-1.5 rounded-full bg-primary motion-safe:animate-pulse" />
        运行中
      </span>
    );
  }
  if (axes.running === "static") {
    return (
      <span className="inline-flex items-center gap-1.5 text-[12px] text-text-2">
        <span className="h-1.5 w-1.5 rounded-full bg-primary" />
        <span className="num">{count}</span> 个运行中
      </span>
    );
  }
  return <span className="text-[12px] text-text-2"><span className="num">{count}</span> 个运行中</span>;
}

function IndeterminateBar({ active }: { active: boolean }) {
  const { axes } = useVisualProto();
  if (axes.running !== "bar" || !active) return null;
  return (
    <div className="absolute inset-x-0 bottom-0 h-0.5 overflow-hidden bg-primary/15" role="presentation">
      <div className="v-indeterminate h-full w-2/5 bg-primary" />
    </div>
  );
}

const STATUS_LABEL: Record<string, string> = {
  queued: "排队中",
  running: "生成中",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
};

function TaskRow({ task }: { task: Pick<TaskItem, "task_id" | "task_type" | "resource_id" | "status" | "project_name"> }) {
  const { axes } = useVisualProto();
  const running = task.status === "running";
  const dotClass =
    task.status === "succeeded"
      ? "bg-good"
      : task.status === "failed"
        ? "bg-danger"
        : task.status === "running"
          ? "bg-primary"
          : "bg-text-4";
  return (
    <li className="relative flex items-center gap-3 px-3 py-2.5 text-[13px]">
      {axes.running === "bar" && running ? (
        <span className="h-1.5 w-1.5 shrink-0" />
      ) : (
        <span
          className={`h-1.5 w-1.5 shrink-0 rounded-full ${dotClass} ${running && axes.running === "pulse" ? "motion-safe:animate-pulse" : ""}`}
        />
      )}
      <span className="min-w-0 flex-1 truncate text-text-2">
        {task.task_type} · <span className="num text-text-3">{task.resource_id}</span>
      </span>
      <span className="shrink-0 text-[12px] text-text-3">{STATUS_LABEL[task.status] ?? task.status}</span>
      <IndeterminateBar active={running} />
    </li>
  );
}

// ---------------------------------------------------------------- 圆角

function RadiusSample({ cls }: { cls: string }) {
  const ref = useRef<HTMLDivElement>(null);
  const [px, setPx] = useState("");
  const { axes } = useVisualProto();
  useLayoutEffect(() => {
    if (ref.current) setPx(getComputedStyle(ref.current).borderTopLeftRadius);
  }, [axes.radius]);
  return (
    <div className="flex flex-col items-center gap-1.5">
      <div ref={ref} className={`h-14 w-20 border border-hairline-strong bg-surface-2 ${cls}`} />
      <div className="text-[12px] text-text-2">{cls}</div>
      <div className="num text-[11px] text-text-3">{px}</div>
    </div>
  );
}

// ---------------------------------------------------------------- 页面

export function VisualSpecimenPage() {
  const { axes } = useVisualProto();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [tasks, setTasks] = useState<TaskItem[]>([]);
  const [legacyConfirm, setLegacyConfirm] = useState(false);

  useEffect(() => {
    void API.listProjects().then((r) => setProjects(r.projects), () => {});
    void API.listTasks({ pageSize: 6 }).then((r) => setTasks(r.items), () => {});
  }, []);

  const firstProject = projects[0]?.title || projects[0]?.name || "示例项目";
  // 真实队列里多半没有正在跑的任务：补几条模拟任务，资源编号取真实项目的格式
  const mockTasks = [
    { task_id: "mock-1", task_type: "storyboard", resource_id: "E1S03", status: "running" as const, project_name: firstProject },
    { task_id: "mock-2", task_type: "video", resource_id: "E1S02", status: "running" as const, project_name: firstProject },
    { task_id: "mock-3", task_type: "video", resource_id: "E1S04", status: "queued" as const, project_name: firstProject },
    { task_id: "mock-4", task_type: "character", resource_id: "林舟", status: "failed" as const, project_name: firstProject },
    { task_id: "mock-5", task_type: "storyboard", resource_id: "E1S01", status: "succeeded" as const, project_name: firstProject },
  ];
  const rows = [...mockTasks, ...tasks];
  const runningCount = rows.filter((t) => t.status === "running").length;
  const thumbs = projects.filter((p) => p.thumbnail).slice(0, 8);

  return (
    <div className="h-dvh overflow-y-auto">
      {/* ------------------------------------------------ 页头：光晕与网格 */}
      <header className="relative overflow-hidden border-b border-hairline-soft">
        <div className="pointer-events-none absolute inset-0" style={ambientGlowStyle({ intensity: 0.22 })} />
        <div className="pointer-events-none absolute inset-0" style={posterGridStyle({ opacity: 0.06 })} />
        <div className="relative mx-auto max-w-[1200px] px-8 pb-10 pt-12">
          <Eyebrow zh="工作台" en="STUDIO" />
          <h1 className="text-[28px] font-semibold tracking-tight text-text">视觉方向样本</h1>
          <p className="mt-2 max-w-[60ch] text-[14px] leading-relaxed text-text-2">
            用底栏切换预设或逐轴覆盖，看同一组界面元素在不同取舍下的样子。真实页面同步生效，可以直接去项目大厅、资产库或设置页对照。
          </p>
        </div>
      </header>

      <main className="mx-auto max-w-[1200px] px-8 pb-40">
        {/* ------------------------------------------------ 光晕 */}
        <Section
          axis="deco"
          title="光晕与网格"
          note={
            <>
              上面的页头用的是现状的 <code>ambientGlowStyle</code>：<code>circle</code> 半径取最远角的 60%，容器越宽，光晕越大，超过容器高度后被底边硬切。下面两条在宽窗口（≥1920）下最明显：上面是现状，下面是「若保留」时的修法（固定尺寸的椭圆，在容器内衰减完）。
            </>
          }
        >
          {axes.deco === "full" ? (
            <div className="grid gap-4">
              <div>
                <Label>现状：随宽度放大，底边硬切</Label>
                <div className="relative h-40 overflow-hidden rounded-lg border border-hairline-soft">
                  <div className="absolute inset-0" style={ambientGlowStyle({ intensity: 0.22 })} />
                </div>
              </div>
              <div>
                <Label>修正：固定尺寸椭圆</Label>
                <div className="relative h-40 overflow-hidden rounded-lg border border-hairline-soft">
                  <div
                    className="absolute inset-0"
                    style={{ background: "radial-gradient(ellipse 640px 150px at 50% 0%, oklch(0.76 0.09 295 / 0.22), transparent)" }}
                  />
                </div>
              </div>
            </div>
          ) : (
            <div className="text-[13px] text-text-3">
              {axes.deco === "ambient" ? "组件内的光晕与网格已隐藏，只保留页面底色的两团渐变。" : "所有装饰已隐藏，页面底色为纯色。"}
            </div>
          )}
        </Section>

        {/* ------------------------------------------------ 小标题 */}
        <Section
          axis="eyebrow"
          title="等宽小标题"
          note="中文界面里，等宽字体对汉字不起作用（回退到系统字体），uppercase 也不起作用，实际只剩 9–10px 的小字号、加宽字距和夹带的英文后缀（如「全部项目 · LIBRARY」）。9–10px 低于本图默认的 11px 下限。英文界面里则是整行大写的等宽字。"
        >
          <div className="grid gap-6 md:grid-cols-3">
            <div>
              <Eyebrow zh="全部项目" en="LIBRARY" />
              <div className="text-[17px] font-semibold text-text">
                项目库 <span className="text-[13px] font-normal text-text-3">{projects.length} 个</span>
              </div>
            </div>
            <div>
              <Eyebrow zh="接着上一次" en="NOW EDITING" />
              <div className="text-[17px] font-semibold text-text">{firstProject}</div>
            </div>
            <div>
              <Eyebrow zh="市场" en="0 ENDPOINTS FROM 1 SOURCE" />
              <div className="text-[17px] font-semibold text-text">端点市场</div>
            </div>
          </div>
          <div className="mt-6 flex gap-8">
            {[
              ["角色", "CAST", 12],
              ["场景", "SCENE", 7],
              ["道具", "PROP", 4],
            ].map(([zh, en, n]) => (
              <div key={en as string}>
                {axes.eyebrow === "mono" ? (
                  <div className="font-mono text-[9px] font-bold uppercase tracking-[0.1em] text-text-3">{en}</div>
                ) : (
                  <div className="text-[12px] text-text-3">{zh}</div>
                )}
                <div className="num mt-0.5 text-[15px] font-semibold text-text">{n}</div>
              </div>
            ))}
          </div>
          <div className="mt-3 text-[12px] text-text-4">统计标签是信息，不是装饰：选「删除」时它们也保留，按本地化常规字显示。</div>
        </Section>

        {/* ------------------------------------------------ 弹层 */}
        <Section
          axis="surface"
          title="弹层材质"
          note="现状的玻璃面板背景不透明度是 0.92–0.96，blur 几乎透不出底下的内容，看得出来的是渐变、顶部渐变线、内高光和很重的阴影。现有代码里 blur 半径有 2、6、8、10、12、14、16、20、28px 九种，没有 token。"
        >
      <div className="mb-4 flex flex-wrap gap-2">
            <Dialog>
              <DialogTrigger render={<Button variant="outline" />}>打开 Dialog</DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>重命名项目</DialogTitle>
                  <DialogDescription>项目名称只影响显示，不影响文件路径。</DialogDescription>
                </DialogHeader>
                <Input defaultValue={firstProject} />
                <DialogFooter>
                  <Button>保存</Button>
                </DialogFooter>
              </DialogContent>
            </Dialog>
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="outline" />}>
                打开下拉 <MoreHorizontal />
              </DropdownMenuTrigger>
              <DropdownMenuContent className="w-56">
                {/* Base UI 的 GroupLabel 必须在 Group 内，否则抛错（Radix 版没有这个约束） */}
                <DropdownMenuGroup>
                  <DropdownMenuLabel>最近项目</DropdownMenuLabel>
                  {projects.slice(0, 4).map((p) => (
                    <DropdownMenuItem key={p.name}>
                      <Film /> {p.title || p.name}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuGroup>
                <DropdownMenuSeparator />
                <DropdownMenuItem variant="destructive">
                  <Trash2 /> 删除项目
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
            <Popover>
              <PopoverTrigger render={<Button variant="outline" />}>打开 Popover</PopoverTrigger>
              <PopoverContent>
                <PopoverHeader>
                  <PopoverTitle>画面比例</PopoverTitle>
                  <PopoverDescription>新生成的分镜使用这个比例，已有分镜不变。</PopoverDescription>
                </PopoverHeader>
              </PopoverContent>
            </Popover>
            <ActionMenu
              label="旧 ActionMenu"
              triggerClassName="inline-flex h-8 items-center rounded-lg border border-hairline px-2.5 text-[13px] text-text-2"
              items={[
                { key: "open", label: "打开", onSelect: () => {} },
                { key: "del", label: "删除", danger: true, onSelect: () => {} },
              ]}
            >
              旧 ActionMenu
            </ActionMenu>
          </div>
          <div className="relative overflow-hidden rounded-xl border border-hairline-soft p-6">
            <div className="pointer-events-none absolute inset-0 grid grid-cols-4 gap-2 p-2 opacity-70">
              {thumbs.map((p) => (
                <img key={p.name} src={p.thumbnail ?? ""} alt="" className="h-full w-full rounded-md object-cover" />
              ))}
            </div>
            <div className="relative flex flex-wrap items-start gap-6">
              <div className="arc-glass-panel relative w-72 overflow-hidden rounded-2xl p-4">
                <span aria-hidden className="arc-glass-hairline" />
                <div className="text-[14px] font-semibold text-text">旧原语静态快照</div>
                <p className="mt-1 text-[13px] text-text-3">GlassModal / GlassPopover 用的 .arc-glass-panel，叠在真实项目封面上。</p>
              </div>
              <div data-slot="popover-content" className="w-72 rounded-lg bg-popover p-4 text-popover-foreground shadow-md ring-1 ring-foreground/10">
                <div className="text-[14px] font-semibold text-text">shadcn Popover 静态快照</div>
                <p className="mt-1 text-[13px] text-text-3">同一位置的 shadcn 弹层，按当前材质渲染。</p>
              </div>
            </div>
          </div>
        </Section>

        {/* ------------------------------------------------ 品牌紫 */}
        <Section
          axis="brand"
          title="品牌紫与按钮"
          note={
            <>
              现状的紫色有 5 个命名变体（<code>primary</code>、<code>primary-2</code>、<code>primary-soft</code>、<code>primary-dim</code>、<code>primary-glow</code>），主按钮是渐变加光晕加悬停上移。纯色方案只需要 <code>primary</code> 一个基色，浅底与描边用透明度修饰（<code>primary/15</code>）表达；克制方案中主按钮改用前景色，紫色只出现在焦点环、选中态与链接上。
            </>
          }
        >
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <Label>旧 PrimaryButton 与 shadcn Button</Label>
              <div className="flex flex-wrap items-center gap-2">
                <PrimaryButton size="sm">生成分镜</PrimaryButton>
                <Button>生成分镜</Button>
                <Button variant="secondary">导出</Button>
                <Button variant="outline">预览</Button>
                <Button variant="ghost">取消</Button>
                <Button variant="link">查看用法</Button>
              </div>
            </div>
            <div>
              <Label>选中态（单选卡片）</Label>
              <div className="flex gap-2">
                <label className={radioCardClass(true)}>
                  <input type="radio" name="v-ratio" defaultChecked className="sr-only" /> 9:16 竖屏
                </label>
                <label className={radioCardClass(false)}>
                  <input type="radio" name="v-ratio" className="sr-only" /> 16:9 横屏
                </label>
              </div>
            </div>
            <div>
              <Label>焦点环（Tab 聚焦下面的输入框）</Label>
              <Input placeholder="项目名称" className="max-w-xs" />
            </div>
            <div>
              <Label>紫色变体色板</Label>
              <div className="flex gap-2">
                {["primary", "primary-2", "primary-soft", "primary-dim", "primary-glow"].map((t) => (
                  <div key={t} className="flex flex-col items-center gap-1">
                    <div
                      className={`h-8 w-12 rounded-md border border-hairline ${
                        axes.brand !== "gradient" && t !== "primary" ? "opacity-25" : ""
                      }`}
                      style={{ background: `var(--color-${t})` }}
                    />
                    <span className="text-[11px] text-text-3">{t}</span>
                  </div>
                ))}
              </div>
              {axes.brand !== "gradient" && <div className="mt-1 text-[12px] text-text-4">淡化的变体在该方案下删除。</div>}
            </div>
          </div>
        </Section>

        {/* ------------------------------------------------ 语义色 */}
        <Section
          axis="danger"
          title="危险与语义色"
          note={
            <>
              现状里「危险」有两套：<code>ConfirmDialog</code> 的 <code>tone=&quot;danger&quot;</code> 实际渲染成琥珀色（warm），<code>PrimaryButton</code> 另有一个红色的 <code>danger</code> tone，shadcn 的 <code>destructive</code> 也是红色。琥珀色同时还表示警告与「过期」，删除和警告因此看起来一样。另外 <code>warn</code> 与 <code>warm</code> 是两个几乎相同的琥珀色。
            </>
          }
        >
          <div className="grid gap-6 md:grid-cols-2">
            <div>
              <Label>删除确认</Label>
              <div className="flex flex-wrap gap-2">
                <Button variant="outline" onClick={() => setLegacyConfirm(true)}>
                  旧 ConfirmDialog
                </Button>
                <AlertDialog>
                  <AlertDialogTrigger render={<Button variant="destructive" />}>
                    <Trash2 /> shadcn AlertDialog
                  </AlertDialogTrigger>
                  <AlertDialogContent>
                    <AlertDialogHeader>
                      <AlertDialogTitle>删除「{firstProject}」？</AlertDialogTitle>
                      <AlertDialogDescription>项目文件与生成的素材会一起删除，无法恢复。</AlertDialogDescription>
                    </AlertDialogHeader>
                    <AlertDialogFooter>
                      <AlertDialogCancel>取消</AlertDialogCancel>
                      <AlertDialogAction variant="destructive">删除项目</AlertDialogAction>
                    </AlertDialogFooter>
                  </AlertDialogContent>
                </AlertDialog>
              </div>
              <ConfirmDialog
                open={legacyConfirm}
                tone="danger"
                title={`删除「${firstProject}」？`}
                description="项目文件与生成的素材会一起删除，无法恢复。"
                confirmLabel="删除项目"
                onConfirm={() => setLegacyConfirm(false)}
                onCancel={() => setLegacyConfirm(false)}
              />
            </div>
            <div>
              <Label>状态徽标</Label>
              <div className="flex flex-wrap gap-2">
                <Badge className="bg-good/15 text-good">已完成</Badge>
                <Badge className="bg-warn/15 text-warn">
                  <AlertTriangle /> 素材已过期
                </Badge>
                <Badge variant="destructive">生成失败</Badge>
                <Badge className="bg-primary/15 text-primary">排队中</Badge>
              </div>
              <div className="mt-4 flex gap-2">
                {["good", "warn", "warm", "danger"].map((t) => (
                  <div key={t} className="flex flex-col items-center gap-1">
                    <div className="h-8 w-12 rounded-md border border-hairline" style={{ background: `var(--color-${t})` }} />
                    <span className="text-[11px] text-text-3">{t}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </Section>

        {/* ------------------------------------------------ 运行中 */}
        <Section
          axis="running"
          title="运行中状态"
          note="现状有 16 处运行中动画：14 处 animate-pulse 透明度呼吸、2 处 animate-ping 扩散环（扩散环是缩放，违反本图「去掉缩放」的默认标准）。无限循环且超过 5 秒的动画按 WCAG 2.2.2 需要能暂停。本地任务队列为空，列表中的 5 条是模拟任务。"
        >
          <div className="max-w-xl overflow-hidden rounded-lg border border-hairline-soft">
            <div className="relative flex items-center justify-between border-b border-hairline-soft px-3 py-2">
              <span className="text-[13px] font-medium text-text">制作队列</span>
              <RunningIndicator count={runningCount} />
            </div>
            <ul className="divide-y divide-hairline-soft">
              {rows.map((t) => (
                <TaskRow key={t.task_id} task={t} />
              ))}
            </ul>
          </div>
        </Section>

        {/* ------------------------------------------------ 圆角 */}
        <Section
          axis="radius"
          title="圆角刻度"
          note="真实页面大多写死了 rounded-[8px]、rounded-[5px] 这类任意值，不随刻度变化；刻度主要影响 shadcn 组件与 rounded-lg、rounded-xl、rounded-2xl 等具名类。"
        >
          <div className="flex flex-wrap gap-6">
            {["rounded-sm", "rounded-md", "rounded-lg", "rounded-xl", "rounded-2xl"].map((c) => (
              <RadiusSample key={c} cls={c} />
            ))}
          </div>
          <div className="mt-6 flex flex-wrap items-center gap-2">
            <Button size="sm">小按钮</Button>
            <Button>按钮</Button>
            <Input className="w-48" placeholder="输入框" />
            <Badge>徽标</Badge>
          </div>
        </Section>
      </main>
    </div>
  );
}
