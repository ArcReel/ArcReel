import React from "react";
import htm from "htm";

import { Button } from "./primitives.js";

const html = htm.bind(React.createElement);

export function CreateProjectModal({
    open,
    createForm,
    setCreateForm,
    creatingProject,
    onClose,
    onSubmit,
}) {
    if (!open) {
        return null;
    }

    return html`
        <div className="fixed inset-0 z-[60] bg-black/50 backdrop-blur-sm flex items-center justify-center px-4">
            <form onSubmit=${onSubmit} className="w-full max-w-lg rounded-2xl app-panel-strong p-5 space-y-4">
                <div className="flex items-center justify-between">
                    <h2 className="text-lg font-semibold">新建漫剧项目</h2>
                    <button type="button" onClick=${onClose} className="h-8 w-8 rounded-lg hover:bg-white/10">×</button>
                </div>

                <label className="text-sm text-slate-300 flex flex-col gap-2">
                    项目名称
                    <input
                        required
                        value=${createForm.name}
                        onChange=${(event) => setCreateForm((previous) => ({ ...previous, name: event.target.value }))}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        placeholder="例如：诡案第一季"
                    />
                </label>

                <label className="text-sm text-slate-300 flex flex-col gap-2">
                    项目标题
                    <input
                        value=${createForm.title}
                        onChange=${(event) => setCreateForm((previous) => ({ ...previous, title: event.target.value }))}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        placeholder="留空默认使用项目名称"
                    />
                </label>

                <div className="grid sm:grid-cols-2 gap-3">
                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        内容模式
                        <select
                            value=${createForm.contentMode}
                            onChange=${(event) => setCreateForm((previous) => ({ ...previous, contentMode: event.target.value }))}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        >
                            <option value="narration">说书+画面（9:16）</option>
                            <option value="drama">剧集动画（16:9）</option>
                        </select>
                    </label>

                    <label className="text-sm text-slate-300 flex flex-col gap-2">
                        视觉风格
                        <select
                            value=${createForm.style}
                            onChange=${(event) => setCreateForm((previous) => ({ ...previous, style: event.target.value }))}
                            className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3"
                        >
                            <option value="Photographic">Photographic</option>
                            <option value="Anime">Anime</option>
                            <option value="3D Animation">3D Animation</option>
                        </select>
                    </label>
                </div>

                <div className="pt-2 flex justify-end gap-2">
                    <${Button} variant="ghost" onClick=${onClose}>取消<//>
                    <${Button} type="submit" disabled=${creatingProject}>${creatingProject ? "创建中..." : "创建项目"}<//>
                </div>
            </form>
        </div>
    `;
}
