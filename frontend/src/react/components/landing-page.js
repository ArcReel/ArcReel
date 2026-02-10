import React, { useEffect, useRef, useState } from "react";
import htm from "htm";
import { Button } from "./primitives.js";

const html = htm.bind(React.createElement);

const HERO_ROLES = [
    { name: "编剧", tone: "from-pink-500/85 to-rose-500/60" },
    { name: "分镜", tone: "from-orange-500/85 to-amber-500/60" },
    { name: "角色", tone: "from-violet-500/85 to-purple-500/60" },
    { name: "导演", tone: "from-cyan-500/85 to-teal-500/60" },
    { name: "美术", tone: "from-yellow-500/85 to-amber-400/60" },
    { name: "剪辑", tone: "from-sky-500/85 to-blue-500/60" },
    { name: "配音", tone: "from-lime-500/80 to-emerald-500/60" },
];

const DISCOVER_ITEMS = [
    { title: "剧情故事短片", tag: "推荐", tone: "from-red-500/75 via-pink-500/60 to-violet-500/65" },
    { title: "古风悬疑", tag: "古风", tone: "from-amber-500/70 via-orange-500/55 to-zinc-700/75" },
    { title: "科幻概念片", tag: "科幻", tone: "from-blue-500/75 via-cyan-500/60 to-indigo-500/70" },
    { title: "神话视觉片", tag: "奇幻", tone: "from-orange-400/75 via-yellow-500/60 to-red-500/70" },
    { title: "都市逆袭", tag: "都市", tone: "from-slate-500/70 via-blue-500/55 to-indigo-600/75" },
    { title: "校园轻喜剧", tag: "青春", tone: "from-cyan-400/70 via-teal-500/55 to-emerald-500/75" },
    { title: "微恐氛围片", tag: "灵异", tone: "from-zinc-700/80 via-slate-700/65 to-indigo-900/80" },
    { title: "热血成长", tag: "热血", tone: "from-rose-500/70 via-fuchsia-500/55 to-orange-500/70" },
    { title: "历史演绎", tag: "历史", tone: "from-stone-500/75 via-amber-600/60 to-zinc-700/80" },
    { title: "治愈日常", tag: "治愈", tone: "from-emerald-400/70 via-cyan-400/55 to-sky-500/70" },
];

function WeChatIcon() {
    return html`
        <svg viewBox="0 0 24 24" className="w-4 h-4" aria-hidden="true" fill="none">
            <path d="M10 4C5.58 4 2 6.92 2 10.5c0 1.9 1.03 3.62 2.67 4.82L4 18l2.59-1.43c1.05.28 2.19.43 3.41.43 4.42 0 8-2.92 8-6.5S14.42 4 10 4Z" fill="currentColor" opacity="0.92"></path>
            <circle cx="7.5" cy="10.3" r="1.02" fill="#05080d"></circle>
            <circle cx="12.5" cy="10.3" r="1.02" fill="#05080d"></circle>
            <path d="M16.8 9.2c2.87.2 5.2 2.1 5.2 4.52 0 1.35-.72 2.56-1.87 3.4L20.6 20l-2-.92a7.24 7.24 0 0 1-1.8.22c-3.42 0-6.2-2.18-6.2-4.88 0-2.4 2.2-4.34 5.05-4.52Z" fill="currentColor"></path>
            <circle cx="15.3" cy="13.8" r="0.92" fill="#05080d"></circle>
            <circle cx="18.8" cy="13.8" r="0.92" fill="#05080d"></circle>
        </svg>
    `;
}

function PlayIcon() {
    return html`
        <svg viewBox="0 0 24 24" className="w-5 h-5 ml-0.5" aria-hidden="true" fill="currentColor">
            <path d="M8 5.7v12.6c0 .55.61.88 1.08.58l9.9-6.3a.67.67 0 0 0 0-1.14l-9.9-6.3A.68.68 0 0 0 8 5.7Z"></path>
        </svg>
    `;
}

export function LandingPage({
    onEnter,
    contactQrSrc = "",
}) {
    const [qrLoadFailed, setQrLoadFailed] = useState(false);
    const [contactCardOpen, setContactCardOpen] = useState(false);
    const [hoverSupported, setHoverSupported] = useState(false);
    const contactAnchorRef = useRef(null);

    useEffect(() => {
        if (typeof window === "undefined" || typeof window.matchMedia !== "function") {
            return undefined;
        }

        const mediaQuery = window.matchMedia("(hover: hover) and (pointer: fine)");
        const syncMode = () => {
            setHoverSupported(mediaQuery.matches);
            if (mediaQuery.matches) {
                setContactCardOpen(false);
            }
        };

        syncMode();
        if (typeof mediaQuery.addEventListener === "function") {
            mediaQuery.addEventListener("change", syncMode);
            return () => mediaQuery.removeEventListener("change", syncMode);
        }

        mediaQuery.addListener(syncMode);
        return () => mediaQuery.removeListener(syncMode);
    }, []);

    useEffect(() => {
        if (!contactCardOpen || hoverSupported) {
            return undefined;
        }

        const handlePointerDown = (event) => {
            if (!contactAnchorRef.current?.contains(event.target)) {
                setContactCardOpen(false);
            }
        };

        document.addEventListener("pointerdown", handlePointerDown);
        return () => document.removeEventListener("pointerdown", handlePointerDown);
    }, [contactCardOpen, hoverSupported]);

    useEffect(() => {
        if (!contactCardOpen) {
            return undefined;
        }

        const handleKeyDown = (event) => {
            if (event.key === "Escape") {
                setContactCardOpen(false);
            }
        };

        window.addEventListener("keydown", handleKeyDown);
        return () => window.removeEventListener("keydown", handleKeyDown);
    }, [contactCardOpen]);

    useEffect(() => {
        setQrLoadFailed(false);
    }, [contactQrSrc, contactCardOpen]);

    return html`
        <div className="landing-shell min-h-screen text-slate-100">
            <header className="fixed top-0 left-0 right-0 z-40 border-b border-white/10 bg-ink-950/75 backdrop-blur-xl">
                <div className="max-w-7xl mx-auto px-6 h-16 flex items-center justify-between">
                    <a
                        href="/"
                        onClick=${(event) => event.preventDefault()}
                        className="flex items-center gap-3"
                    >
                        <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-neon-400 to-amberx-400"></span>
                        <span className="text-lg font-semibold tracking-wide">ArcReel</span>
                    </a>

                    <div className="flex items-center gap-3">
                        <div
                            ref=${contactAnchorRef}
                            className="relative"
                            onMouseEnter=${() => {
                                if (hoverSupported) {
                                    setContactCardOpen(true);
                                }
                            }}
                            onMouseLeave=${() => {
                                if (hoverSupported) {
                                    setContactCardOpen(false);
                                }
                            }}
                        >
                            <button
                                type="button"
                                title="联系我们"
                                aria-label="联系我们"
                                aria-haspopup="dialog"
                                aria-expanded=${contactCardOpen}
                                onClick=${() => {
                                    if (!hoverSupported) {
                                        setContactCardOpen((value) => !value);
                                    }
                                }}
                                className="h-9 px-3 rounded-xl border border-white/20 bg-white/5 text-slate-100 hover:border-neon-400/60 hover:text-neon-300 transition-colors inline-flex items-center gap-2"
                            >
                                <${WeChatIcon} />
                                <span className="hidden sm:inline text-sm">联系我们</span>
                            </button>

                            <div
                                role="dialog"
                                aria-label="微信二维码"
                                className=${`absolute right-0 top-12 w-60 rounded-2xl border border-white/15 bg-ink-900/95 backdrop-blur-xl p-3 shadow-2xl transition-all duration-150 ${
                                    contactCardOpen ? "opacity-100 translate-y-0 pointer-events-auto" : "opacity-0 -translate-y-1 pointer-events-none"
                                }`}
                            >
                                <div className="rounded-xl bg-white p-2 flex items-center justify-center">
                                    ${
                                        contactQrSrc && !qrLoadFailed
                                            ? html`<img src=${contactQrSrc} alt="微信二维码" onError=${() => setQrLoadFailed(true)} className="w-48 h-48 object-contain" />`
                                            : html`
                                                <div className="w-48 h-48 rounded-lg border border-dashed border-slate-300/70 bg-slate-100 text-slate-600 text-xs flex items-center justify-center text-center px-4">
                                                    二维码文件未配置
                                                </div>
                                            `
                                    }
                                </div>
                                <p className="mt-2 text-xs text-slate-300 text-center">扫码添加微信</p>
                            </div>
                        </div>

                        <${Button} onClick=${onEnter} size="sm">进入管理台<//>
                    </div>
                </div>
            </header>

            <main>
                <section className="relative min-h-screen overflow-hidden pt-24 pb-16 flex items-center">
                    <div className="landing-grid pointer-events-none"></div>
                    <div className="landing-curve pointer-events-none"></div>
                    <div className="landing-orb landing-orb-a"></div>
                    <div className="landing-orb landing-orb-b"></div>

                    <div className="relative z-10 max-w-6xl mx-auto px-6 w-full text-center">
                        <p className="inline-flex items-center gap-2 px-3 py-1 rounded-full border border-neon-500/35 bg-neon-500/10 text-neon-300 text-xs tracking-[0.18em] uppercase">
                            ArcReel Studio
                        </p>
                        <h1 className="landing-slogan mt-6 text-[2.65rem] leading-[1.05] md:text-7xl md:leading-[1.02] font-bold tracking-tight">
                            Make Story Reels
                            <br />
                            At Studio Speed
                        </h1>
                        <p className="mt-6 text-base md:text-xl text-slate-300/85 max-w-2xl mx-auto">
                            导演，我们是您的专业 AI 漫剧制作团队。
                        </p>
                        <div className="mt-8 flex justify-center">
                            <${Button} onClick=${onEnter} size="lg" className="min-w-40">进入管理台<//>
                        </div>

                        <div className="mt-12 md:mt-16 flex flex-wrap justify-center items-end gap-3 md:gap-5">
                            ${HERO_ROLES.map((role, index) => html`
                                <article
                                    key=${role.name}
                                    className=${`w-[82px] md:w-[110px] rounded-[1.6rem] border border-white/15 bg-gradient-to-b ${role.tone} shadow-[0_18px_36px_rgba(0,0,0,0.35)]`}
                                    style=${{ height: `${88 + (index % 3) * 10}px` }}
                                >
                                    <div className="h-full flex items-end justify-center pb-3">
                                        <span className="text-xs md:text-sm font-semibold text-white/95">${role.name}</span>
                                    </div>
                                </article>
                            `)}
                        </div>
                    </div>
                </section>

                <section className="pb-24 md:pb-32">
                    <div className="max-w-7xl mx-auto px-6">
                        <h2 className="text-center text-4xl md:text-6xl font-semibold tracking-tight">发现更多</h2>
                        <div className="mt-10 grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-5">
                            ${DISCOVER_ITEMS.map((item) => html`
                                <article key=${item.title} className="group relative overflow-hidden rounded-2xl border border-white/15 bg-ink-900/60 transition-transform duration-200 hover:-translate-y-0.5 hover:border-neon-400/55">
                                    <div className=${`h-48 bg-gradient-to-br ${item.tone}`}></div>
                                    <div className="absolute inset-0 bg-gradient-to-t from-black/70 via-black/10 to-transparent"></div>
                                    <span className="absolute left-3 top-3 px-2 py-1 text-[10px] rounded-full border border-white/20 bg-black/35 text-white/90">
                                        ${item.tag}
                                    </span>
                                    <div className="absolute inset-0 flex items-center justify-center">
                                        <div className="w-11 h-11 rounded-full bg-white/80 text-ink-950 flex items-center justify-center shadow-lg">
                                            <${PlayIcon} />
                                        </div>
                                    </div>
                                    <div className="absolute left-0 right-0 bottom-0 p-4">
                                        <h3 className="text-base font-semibold text-white">${item.title}</h3>
                                    </div>
                                </article>
                            `)}
                        </div>
                    </div>
                </section>
            </main>
        </div>
    `;
}
