import React from "react";
import htm from "htm";
import { Button } from "./primitives.js";

const html = htm.bind(React.createElement);

export function LandingPage({ onEnter, onAssistant, onUsage }) {
    return html`
        <div className="landing-shell min-h-screen text-slate-100">
            <header className="fixed top-0 left-0 right-0 z-40 border-b border-white/10 bg-ink-950/80 backdrop-blur-xl">
                <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
                    <a href="/" onClick=${(event) => {
                        event.preventDefault();
                    }} className="flex items-center gap-3">
                        <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-neon-400 to-amberx-400"></span>
                        <span className="text-lg font-semibold tracking-wide">漫剧工厂</span>
                    </a>
                    <nav className="hidden md:flex items-center gap-6 text-sm text-slate-300">
                        <a href="#value" className="hover:text-white transition-colors">平台价值</a>
                        <a href="#cases" className="hover:text-white transition-colors">案例</a>
                        <a href="#contact" className="hover:text-white transition-colors">公众号</a>
                    </nav>
                    <${Button} onClick=${onEnter} size="sm">进入管理台<//>
                </div>
            </header>

            <main>
                <section className="relative min-h-screen overflow-hidden flex items-center pt-16">
                    <div className="landing-grid"></div>
                    <div className="landing-orb landing-orb-a"></div>
                    <div className="landing-orb landing-orb-b"></div>
                    <div className="max-w-7xl mx-auto px-6 w-full">
                        <div className="grid lg:grid-cols-[1.2fr_0.8fr] gap-10 items-center">
                            <article>
                                <p className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-neon-500/30 bg-neon-500/10 text-neon-300 text-xs tracking-wide">AI 漫剧生产工作台</p>
                                <h1 className="mt-6 text-4xl md:text-6xl leading-tight font-semibold tracking-tight">把小说变成可量产的<br /><span className="text-neon-300">短视频漫剧</span></h1>
                                <p className="mt-6 text-base md:text-lg text-slate-300 max-w-2xl leading-relaxed">从剧本拆分、人物与线索一致性，到分镜和视频生成，全流程项目制管理。你的团队不再依赖零散脚本和表格协作。</p>
                                <div className="mt-8 flex flex-wrap items-center gap-4">
                                    <${Button} onClick=${onEnter} size="lg">立即体验后台<//>
                                    <${Button} variant="outline" onClick=${onAssistant} size="lg">对话管理<//>
                                    <${Button} variant="outline" onClick=${onUsage} size="lg">费用统计<//>
                                </div>
                            </article>

                            <aside id="contact" className="rounded-3xl border border-white/10 bg-gradient-to-b from-white/10 to-white/5 p-6 backdrop-blur-xl">
                                <p className="text-sm text-slate-300">扫码关注公众号</p>
                                <h2 className="mt-2 text-2xl font-semibold">获取内测与更新</h2>
                                <div className="mt-6 rounded-2xl border border-dashed border-neon-300/50 bg-ink-900/60 p-6 flex items-center justify-center">
                                    <div className="w-52 h-52 rounded-xl bg-white/90 text-ink-950 flex flex-col items-center justify-center">
                                        <div className="w-36 h-36 border-8 border-ink-900 rounded-xl"></div>
                                        <p className="mt-3 text-xs font-semibold">公众号二维码占位</p>
                                    </div>
                                </div>
                                <p className="mt-4 text-xs text-slate-400 leading-relaxed">前期可通过公众号发放邀请码。正式开放注册后，此处替换为真实二维码与客服入口。</p>
                            </aside>
                        </div>
                    </div>
                </section>

                <section id="value" className="py-24 border-t border-white/10">
                    <div className="max-w-7xl mx-auto px-6">
                        <h2 className="text-3xl md:text-4xl font-semibold tracking-tight">平台价值</h2>
                        <div className="mt-10 grid md:grid-cols-3 gap-6">
                            <article className="rounded-2xl border border-white/10 bg-white/5 p-6">
                                <h3 className="text-xl font-semibold">项目制管理</h3>
                                <p className="mt-3 text-slate-300">每个漫剧是一个独立项目，内含概览、任务、线索、剧集/场景，进度一眼可见。</p>
                            </article>
                            <article className="rounded-2xl border border-white/10 bg-white/5 p-6">
                                <h3 className="text-xl font-semibold">助手驱动工作流</h3>
                                <p className="mt-3 text-slate-300">通过 Skills 对话驱动生成任务，支持流式反馈和工具调用过程可视化。</p>
                            </article>
                            <article className="rounded-2xl border border-white/10 bg-white/5 p-6">
                                <h3 className="text-xl font-semibold">费用透明可控</h3>
                                <p className="mt-3 text-slate-300">按项目追踪图像/视频调用与成本，便于团队优化预算和产出质量。</p>
                            </article>
                        </div>
                    </div>
                </section>

                <section id="cases" className="py-24 border-t border-white/10">
                    <div className="max-w-7xl mx-auto px-6">
                        <h2 className="text-3xl md:text-4xl font-semibold tracking-tight">案例分享</h2>
                        <div className="mt-10 grid md:grid-cols-3 gap-6">
                            <article className="rounded-2xl border border-white/10 overflow-hidden bg-ink-900/50"><div className="h-44 bg-gradient-to-br from-neon-500/40 to-amberx-500/30"></div><div className="p-5"><h3 className="font-semibold">悬疑古风漫剧</h3></div></article>
                            <article className="rounded-2xl border border-white/10 overflow-hidden bg-ink-900/50"><div className="h-44 bg-gradient-to-br from-amberx-500/35 to-neon-500/20"></div><div className="p-5"><h3 className="font-semibold">都市逆袭系列</h3></div></article>
                            <article className="rounded-2xl border border-white/10 overflow-hidden bg-ink-900/50"><div className="h-44 bg-gradient-to-br from-neon-400/35 to-cyan-400/20"></div><div className="p-5"><h3 className="font-semibold">校园轻喜剧</h3></div></article>
                        </div>
                    </div>
                </section>
            </main>

            <footer className="border-t border-white/10 py-10">
                <div className="max-w-7xl mx-auto px-6 flex flex-col md:flex-row md:items-center md:justify-between gap-4 text-sm text-slate-400">
                    <p>© 2026 漫剧工厂 · AI 漫剧生产平台</p>
                    <div className="flex items-center gap-5">
                        <button onClick=${onEnter} className="hover:text-white">管理后台</button>
                        <button onClick=${onAssistant} className="hover:text-white">对话管理</button>
                        <button onClick=${onUsage} className="hover:text-white">费用统计</button>
                    </div>
                </div>
            </footer>
        </div>
    `;
}
