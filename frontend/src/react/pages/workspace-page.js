import React, { useCallback, useEffect, useMemo, useState } from "react";
import htm from "htm";

import { PROJECT_TABS } from "../constants.js";
import { cn, progressPercent } from "../utils.js";
import { Badge, Button, Card, EmptyState } from "../components/primitives.js";

const html = htm.bind(React.createElement);

const DEFAULT_CHARACTER_FORM = {
    name: "",
    description: "",
    voiceStyle: "",
};

const DEFAULT_CLUE_FORM = {
    name: "",
    clueType: "prop",
    importance: "major",
    description: "",
};

const DEFAULT_PROJECT_FORM = {
    title: "",
    style: "Photographic",
    contentMode: "narration",
};

const DEFAULT_OVERVIEW_FORM = {
    synopsis: "",
    genre: "",
    theme: "",
    worldSetting: "",
};

const DEFAULT_SOURCE_EDITOR = {
    open: false,
    filename: "",
    content: "",
    editingExisting: false,
};

const DEFAULT_DRAFT_EDITOR = {
    open: false,
    episode: "",
    step: 1,
    content: "",
    contentMode: "narration",
};

const DEFAULT_PREVIEW_MEDIA = {
    open: false,
    type: "image",
    url: "",
    title: "",
};

function itemKey(scriptFile, itemId) {
    return `${scriptFile}::${itemId}`;
}

function resolveFileUrl(projectName, filePath) {
    if (!projectName || !filePath || !window.API || typeof window.API.getFileUrl !== "function") {
        return "";
    }
    return window.API.getFileUrl(projectName, filePath);
}

function readImageScene(prompt) {
    if (typeof prompt === "string") {
        return prompt;
    }
    if (prompt && typeof prompt === "object" && !Array.isArray(prompt)) {
        return String(prompt.scene || "");
    }
    return "";
}

function readVideoAction(prompt) {
    if (typeof prompt === "string") {
        return prompt;
    }
    if (prompt && typeof prompt === "object" && !Array.isArray(prompt)) {
        return String(prompt.action || "");
    }
    return "";
}

function normalizeImagePrompt(basePrompt, sceneText) {
    const scene = String(sceneText || "").trim();
    if (basePrompt && typeof basePrompt === "object" && !Array.isArray(basePrompt)) {
        return {
            ...basePrompt,
            scene,
        };
    }
    return {
        scene,
        composition: {
            shot_type: "Medium Shot",
            lighting: "",
            ambiance: "",
        },
    };
}

function normalizeVideoPrompt(basePrompt, actionText) {
    const action = String(actionText || "").trim();
    if (basePrompt && typeof basePrompt === "object" && !Array.isArray(basePrompt)) {
        return {
            ...basePrompt,
            action,
        };
    }
    return {
        action,
        camera_motion: "Static",
        ambiance_audio: "",
        dialogue: [],
    };
}

function createCharacterDrafts(characters) {
    const nextDrafts = {};
    Object.entries(characters || {}).forEach(([name, character]) => {
        nextDrafts[name] = {
            description: character.description || "",
            voiceStyle: character.voice_style || "",
        };
    });
    return nextDrafts;
}

function createClueDrafts(clues) {
    const nextDrafts = {};
    Object.entries(clues || {}).forEach(([name, clue]) => {
        nextDrafts[name] = {
            clueType: clue.type || "prop",
            importance: clue.importance || "major",
            description: clue.description || "",
        };
    });
    return nextDrafts;
}

function createItemDrafts(scripts) {
    const nextDrafts = {};

    Object.entries(scripts || {}).forEach(([scriptFile, script]) => {
        const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
        const items = isNarration ? script.segments || [] : script.scenes || [];

        items.forEach((item) => {
            const itemId = item.segment_id || item.scene_id;
            if (!itemId) {
                return;
            }

            nextDrafts[itemKey(scriptFile, itemId)] = {
                duration: String(item.duration_seconds || 4),
                segmentBreak: Boolean(item.segment_break),
                imageScene: readImageScene(item.image_prompt),
                videoAction: readVideoAction(item.video_prompt),
            };
        });
    });

    return nextDrafts;
}

function createProjectForm(project) {
    if (!project) {
        return { ...DEFAULT_PROJECT_FORM };
    }
    return {
        title: project.title || "",
        style: project.style || "Photographic",
        contentMode: project.content_mode || "narration",
    };
}

function createOverviewForm(project) {
    const overview = project?.overview || {};
    return {
        synopsis: overview.synopsis || "",
        genre: overview.genre || "",
        theme: overview.theme || "",
        worldSetting: overview.world_setting || "",
    };
}

function getDraftSteps(contentMode) {
    if (contentMode === "narration") {
        return [
            { step: 1, label: "片段拆分" },
            { step: 2, label: "角色线索表" },
        ];
    }
    return [
        { step: 1, label: "规范化剧本" },
        { step: 2, label: "镜头预算" },
        { step: 3, label: "角色线索表" },
    ];
}

function ProjectOverview({
    currentProjectData,
    currentProjectName,
    projectForm,
    onProjectFormChange,
    onSaveProject,
    overviewForm,
    onOverviewFormChange,
    onSaveOverview,
    onGenerateOverview,
    styleDescription,
    onStyleDescriptionChange,
    onUploadStyleImage,
    onSaveStyleDescription,
    onDeleteStyleImage,
    sourceFiles,
    sourceEditor,
    onOpenPreview,
    onOpenSourceEditor,
    onSourceEditorChange,
    onCancelSourceEditor,
    onSaveSourceEditor,
    onDeleteSourceFile,
    onUploadSource,
    busy,
}) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="未加载项目"
                description="请选择项目后查看概览。"
            />
        `;
    }

    const progress = currentProjectData.status?.progress || {};
    const styleImageUrl = resolveFileUrl(currentProjectName, currentProjectData.style_image);
    const stats = [
        { label: "人物", data: progress.characters || { total: 0, completed: 0 }, color: "bg-neon-400" },
        { label: "线索", data: progress.clues || { total: 0, completed: 0 }, color: "bg-cyan-400" },
        { label: "分镜", data: progress.storyboards || { total: 0, completed: 0 }, color: "bg-sky-400" },
        { label: "视频", data: progress.videos || { total: 0, completed: 0 }, color: "bg-emerald-400" },
    ];

    return html`
        <div className="space-y-3">
            <div className="grid grid-cols-2 lg:grid-cols-4 gap-2">
                ${stats.map((item) => {
                    const completed = item.data.completed || 0;
                    const total = item.data.total || 0;
                    const percent = progressPercent(completed, total);

                    return html`
                        <article key=${item.label} className="rounded-lg border border-white/10 bg-white/5 p-2.5">
                            <div className="flex items-center justify-between gap-2">
                                <p className="text-xs text-slate-400">${item.label}</p>
                                <p className="text-lg font-semibold">${completed}/${total}</p>
                            </div>
                            <div className="mt-1.5 h-1 rounded-full bg-white/10 overflow-hidden">
                                <div className=${cn("h-full rounded-full", item.color)} style=${{ width: `${percent}%` }}></div>
                            </div>
                        </article>
                    `;
                })}
            </div>

            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
                <h3 className="text-base font-semibold">项目信息</h3>
                <div className="grid lg:grid-cols-3 gap-3">
                    <input
                        value=${projectForm.title}
                        onChange=${(event) => onProjectFormChange("title", event.target.value)}
                        placeholder="项目标题"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <select
                        value=${projectForm.style}
                        onChange=${(event) => onProjectFormChange("style", event.target.value)}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    >
                        <option value="Photographic">Photographic</option>
                        <option value="Anime">Anime</option>
                        <option value="3D Animation">3D Animation</option>
                    </select>
                    <select
                        value=${projectForm.contentMode}
                        onChange=${(event) => onProjectFormChange("contentMode", event.target.value)}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    >
                        <option value="narration">说书+画面（9:16）</option>
                        <option value="drama">剧集动画（16:9）</option>
                    </select>
                </div>
                <div>
                    <${Button} size="sm" variant="outline" onClick=${onSaveProject} disabled=${busy}>保存项目信息<//>
                </div>
            </article>

            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
                <div className="flex items-center justify-between gap-2">
                    <h3 className="text-base font-semibold">剧情概述</h3>
                    <div className="flex items-center gap-2">
                        <${Button} size="sm" variant="outline" onClick=${onGenerateOverview} disabled=${busy}>重新生成<//>
                        <${Button} size="sm" onClick=${onSaveOverview} disabled=${busy}>保存概述<//>
                    </div>
                </div>
                <textarea
                    value=${overviewForm.synopsis}
                    onChange=${(event) => onOverviewFormChange("synopsis", event.target.value)}
                    placeholder="故事梗概"
                    className="w-full min-h-28 rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-sm"
                ></textarea>
                <div className="grid lg:grid-cols-3 gap-3">
                    <input
                        value=${overviewForm.genre}
                        onChange=${(event) => onOverviewFormChange("genre", event.target.value)}
                        placeholder="题材类型"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <input
                        value=${overviewForm.theme}
                        onChange=${(event) => onOverviewFormChange("theme", event.target.value)}
                        placeholder="核心主题"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <input
                        value=${overviewForm.worldSetting}
                        onChange=${(event) => onOverviewFormChange("worldSetting", event.target.value)}
                        placeholder="世界观设定"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                </div>
            </article>

            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
                <div className="flex items-center justify-between gap-2">
                    <h3 className="text-base font-semibold">风格参考图</h3>
                    <div className="flex items-center gap-2">
                        <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                            上传图片
                            <input
                                type="file"
                                accept="image/*"
                                className="hidden"
                                onChange=${(event) => {
                                    const file = event.target.files?.[0];
                                    if (file) {
                                        onUploadStyleImage(file);
                                    }
                                    event.target.value = "";
                                }}
                            />
                        </label>
                        <${Button} size="sm" variant="outline" onClick=${onSaveStyleDescription} disabled=${busy}>保存描述<//>
                        <${Button} size="sm" variant="danger" onClick=${onDeleteStyleImage} disabled=${busy}>删除<//>
                    </div>
                </div>
                <div className="grid lg:grid-cols-[112px_1fr] gap-3">
                    <button
                        type="button"
                        onClick=${() => styleImageUrl && onOpenPreview(styleImageUrl, "image", "风格参考图")}
                        className="h-28 w-28 rounded-lg border border-white/10 bg-ink-900/70 overflow-hidden"
                    >
                        ${styleImageUrl
                            ? html`<img src=${styleImageUrl} alt="style reference" className="w-full h-full object-cover" />`
                            : html`<div className="w-full h-full flex items-center justify-center text-xs text-slate-500">暂无风格图</div>`}
                    </button>
                    <textarea
                        value=${styleDescription}
                        onChange=${(event) => onStyleDescriptionChange(event.target.value)}
                        placeholder="风格描述（可编辑）"
                        className="w-full min-h-28 rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-sm"
                    ></textarea>
                </div>
            </article>

            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
                <div className="flex items-center justify-between gap-2">
                    <h3 className="text-base font-semibold">源文件</h3>
                    <div className="flex items-center gap-2">
                        <${Button} size="sm" variant="outline" onClick=${() => onOpenSourceEditor()} disabled=${busy}>新建<//>
                        <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                            上传
                            <input
                                type="file"
                                accept=".txt,.md,.doc,.docx"
                                className="hidden"
                                onChange=${(event) => {
                                    const file = event.target.files?.[0];
                                    if (file) {
                                        onUploadSource(file);
                                    }
                                    event.target.value = "";
                                }}
                            />
                        </label>
                    </div>
                </div>

                <div className="rounded-xl border border-white/10 bg-ink-900/40 max-h-48 overflow-y-auto">
                    ${sourceFiles.length === 0
                        ? html`<div className="px-3 py-6 text-center text-xs text-slate-500">暂无源文件</div>`
                        : html`
                              ${sourceFiles.map((file) => html`
                                  <div key=${file.name} className="px-3 py-2 border-b border-white/5 last:border-b-0 flex items-center justify-between gap-3">
                                      <span className="text-xs text-slate-200 truncate">${file.name}</span>
                                      <div className="flex items-center gap-2 shrink-0">
                                          <button
                                              onClick=${() => onOpenSourceEditor(file.name)}
                                              className="text-xs text-slate-300 hover:text-neon-300"
                                          >
                                              编辑
                                          </button>
                                          <button
                                              onClick=${() => onDeleteSourceFile(file.name)}
                                              className="text-xs text-red-300 hover:text-red-200"
                                          >
                                              删除
                                          </button>
                                      </div>
                                  </div>
                              `)}
                          `}
                </div>

                ${sourceEditor.open
                    ? html`
                          <div className="space-y-2 rounded-xl border border-white/10 bg-ink-900/50 p-3">
                              <input
                                  value=${sourceEditor.filename}
                                  onChange=${(event) => onSourceEditorChange("filename", event.target.value)}
                                  disabled=${sourceEditor.editingExisting}
                                  placeholder="文件名（.txt/.md）"
                                  className="w-full h-9 rounded-lg border border-white/15 bg-ink-900/70 px-2 text-sm disabled:opacity-60"
                              />
                              <textarea
                                  value=${sourceEditor.content}
                                  onChange=${(event) => onSourceEditorChange("content", event.target.value)}
                                  className="w-full min-h-36 rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-sm"
                              ></textarea>
                              <div className="flex items-center gap-2">
                                  <${Button} size="sm" onClick=${onSaveSourceEditor} disabled=${busy}>保存文件<//>
                                  <${Button} size="sm" variant="ghost" onClick=${onCancelSourceEditor} disabled=${busy}>取消<//>
                              </div>
                          </div>
                      `
                    : null}
            </article>
        </div>
    `;
}

function ProjectTasks({
    currentProjectData,
    currentProjectName,
    characterDrafts,
    newCharacter,
    onNewCharacterChange,
    onCreateCharacter,
    onCharacterDraftChange,
    onSaveCharacter,
    onDeleteCharacter,
    onUploadCharacterImage,
    onUploadCharacterReference,
    onGenerateCharacter,
    onOpenPreview,
    busy,
}) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无任务数据"
                description="项目加载后会显示任务看板。"
            />
        `;
    }
    const characters = Object.entries(currentProjectData.characters || {});

    return html`
        <div className="space-y-3">
            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-4">
                <div className="flex items-center justify-between gap-3">
                    <h3 className="text-lg font-semibold">人物管理</h3>
                    <span className="text-xs text-slate-400">${currentProjectName || "-"}</span>
                </div>

                <div className="grid lg:grid-cols-4 gap-3">
                    <input
                        value=${newCharacter.name}
                        onChange=${(event) => onNewCharacterChange("name", event.target.value)}
                        placeholder="人物名称"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <input
                        value=${newCharacter.voiceStyle}
                        onChange=${(event) => onNewCharacterChange("voiceStyle", event.target.value)}
                        placeholder="声音风格（可选）"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <input
                        value=${newCharacter.description}
                        onChange=${(event) => onNewCharacterChange("description", event.target.value)}
                        placeholder="人物描述"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm lg:col-span-2"
                    />
                </div>
                <div>
                    <${Button} size="sm" onClick=${onCreateCharacter} disabled=${busy}>新增人物<//>
                </div>

                ${characters.length === 0
                    ? html`<p className="text-sm text-slate-400">暂无人物，可在上方新增并上传/生成人物图。</p>`
                    : html`
                          <div className="grid lg:grid-cols-2 gap-4">
                              ${characters.map(([name, character]) => {
                                  const draft = characterDrafts[name] || {
                                      description: "",
                                      voiceStyle: "",
                                  };
                                  const imageUrl = resolveFileUrl(currentProjectName, character.character_sheet);
                                  const refImageUrl = resolveFileUrl(currentProjectName, character.reference_image);

                                  return html`
                                      <article key=${name} className="rounded-xl border border-white/10 bg-ink-900/50 p-3 space-y-3">
                                          <div className="flex items-center justify-between gap-3">
                                              <h4 className="font-semibold truncate">${name}</h4>
                                              <${Badge} className="bg-white/10 border border-white/10 text-slate-200">人物<//>
                                          </div>
                                          <div className="flex items-start gap-3">
                                              <div className="w-16 shrink-0 space-y-2">
                                                  <button
                                                      type="button"
                                                      onClick=${() => imageUrl && onOpenPreview(imageUrl, "image", `${name} 人物图`)}
                                                      className="h-20 w-full rounded-lg border border-white/10 bg-ink-900/80 overflow-hidden"
                                                  >
                                                      ${imageUrl
                                                          ? html`<img src=${imageUrl} alt=${name} className="w-full h-full object-cover" />`
                                                          : html`<div className="w-full h-full flex items-center justify-center text-[10px] text-slate-500">人物图</div>`}
                                                  </button>
                                                  <button
                                                      type="button"
                                                      onClick=${() => refImageUrl && onOpenPreview(refImageUrl, "image", `${name} 参考图`)}
                                                      className="h-20 w-full rounded-lg border border-white/10 bg-ink-900/80 overflow-hidden"
                                                  >
                                                      ${refImageUrl
                                                          ? html`<img src=${refImageUrl} alt=${`${name} reference`} className="w-full h-full object-cover" />`
                                                          : html`<div className="w-full h-full flex items-center justify-center text-[10px] text-slate-500">参考图</div>`}
                                                  </button>
                                              </div>
                                              <div className="flex-1 min-w-0 space-y-2">
                                                  <textarea
                                                      value=${draft.description}
                                                      onChange=${(event) => onCharacterDraftChange(name, "description", event.target.value)}
                                                      className="w-full min-h-16 rounded-xl border border-white/15 bg-ink-900/70 px-3 py-2 text-xs"
                                                      placeholder="人物描述"
                                                  ></textarea>
                                                  <input
                                                      value=${draft.voiceStyle}
                                                      onChange=${(event) => onCharacterDraftChange(name, "voiceStyle", event.target.value)}
                                                      className="w-full h-9 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-xs"
                                                      placeholder="声音风格"
                                                  />
                                              </div>
                                          </div>

                                          <div className="flex flex-wrap gap-2">
                                              <${Button} size="sm" variant="outline" onClick=${() => onSaveCharacter(name)} disabled=${busy}>保存<//>
                                              <${Button} size="sm" onClick=${() => onGenerateCharacter(name)} disabled=${busy}>生成<//>
                                              <${Button} size="sm" variant="danger" onClick=${() => onDeleteCharacter(name)} disabled=${busy}>删除<//>
                                              <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                                                  人物图
                                                  <input
                                                      type="file"
                                                      accept="image/*"
                                                      className="hidden"
                                                      onChange=${(event) => {
                                                          const file = event.target.files?.[0];
                                                          if (file) {
                                                              onUploadCharacterImage(name, file);
                                                          }
                                                          event.target.value = "";
                                                      }}
                                                  />
                                              </label>
                                              <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                                                  参考图
                                                  <input
                                                      type="file"
                                                      accept="image/*"
                                                      className="hidden"
                                                      onChange=${(event) => {
                                                          const file = event.target.files?.[0];
                                                          if (file) {
                                                              onUploadCharacterReference(name, file);
                                                          }
                                                          event.target.value = "";
                                                      }}
                                                  />
                                              </label>
                                          </div>
                                      </article>
                                  `;
                              })}
                          </div>
                      `}
            </article>
        </div>
    `;
}

function ProjectClues({
    currentProjectData,
    currentProjectName,
    clueDrafts,
    newClue,
    onNewClueChange,
    onCreateClue,
    onClueDraftChange,
    onSaveClue,
    onDeleteClue,
    onUploadClueImage,
    onGenerateClue,
    onOpenPreview,
    busy,
}) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无线索数据"
                description="请选择项目后查看线索。"
            />
        `;
    }

    const clues = Object.entries(currentProjectData.clues || {});

    return html`
        <div className="space-y-4">
            <article className="rounded-2xl border border-white/10 bg-white/5 p-4 space-y-3">
                <h3 className="text-lg font-semibold">新增线索</h3>
                <div className="grid lg:grid-cols-4 gap-3">
                    <input
                        value=${newClue.name}
                        onChange=${(event) => onNewClueChange("name", event.target.value)}
                        placeholder="线索名称"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                    <select
                        value=${newClue.clueType}
                        onChange=${(event) => onNewClueChange("clueType", event.target.value)}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    >
                        <option value="prop">道具</option>
                        <option value="location">地点</option>
                    </select>
                    <select
                        value=${newClue.importance}
                        onChange=${(event) => onNewClueChange("importance", event.target.value)}
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    >
                        <option value="major">主要</option>
                        <option value="minor">次要</option>
                    </select>
                    <input
                        value=${newClue.description}
                        onChange=${(event) => onNewClueChange("description", event.target.value)}
                        placeholder="线索描述"
                        className="h-10 rounded-xl border border-white/15 bg-ink-900/70 px-3 text-sm"
                    />
                </div>
                <div>
                    <${Button} size="sm" onClick=${onCreateClue} disabled=${busy}>新增线索<//>
                </div>
            </article>

            ${clues.length === 0
                ? html`
                      <${EmptyState}
                          title="暂无线索"
                          description="可在上方新增线索，支持上传/生成线索图。"
                      />
                  `
                : html`
                      <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3">
                          ${clues.map(([name, clue]) => {
                              const draft = clueDrafts[name] || {
                                  clueType: "prop",
                                  importance: "major",
                                  description: "",
                              };
                              const imageUrl = resolveFileUrl(currentProjectName, clue.clue_sheet);
                              const clueTypeLabel = draft.clueType === "location" ? "地点" : "道具";

                              return html`
                                  <article key=${name} className="rounded-xl border border-white/10 bg-white/5 p-3 space-y-2">
                                      <div className="flex items-center justify-between gap-2">
                                          <h4 className="font-semibold truncate">${name}</h4>
                                          <${Badge} className="bg-cyan-500/15 text-cyan-300 border border-cyan-400/30">${clueTypeLabel}<//>
                                      </div>

                                      <div className="flex items-start gap-3">
                                          <button
                                              type="button"
                                              onClick=${() => imageUrl && onOpenPreview(imageUrl, "image", `${name} 线索图`)}
                                              className="w-24 h-16 rounded-lg border border-white/10 bg-ink-900/70 overflow-hidden shrink-0"
                                          >
                                              ${imageUrl
                                                  ? html`<img src=${imageUrl} alt=${name} className="w-full h-full object-cover" />`
                                                  : html`<div className="w-full h-full flex items-center justify-center text-[10px] text-slate-500">线索图</div>`}
                                          </button>
                                          <div className="flex-1 min-w-0 space-y-2">
                                              <div className="grid grid-cols-2 gap-2">
                                                  <select
                                                      value=${draft.clueType}
                                                      onChange=${(event) => onClueDraftChange(name, "clueType", event.target.value)}
                                                      className="h-8 rounded-lg border border-white/15 bg-ink-900/70 px-2 text-xs"
                                                  >
                                                      <option value="prop">道具</option>
                                                      <option value="location">地点</option>
                                                  </select>
                                                  <select
                                                      value=${draft.importance}
                                                      onChange=${(event) => onClueDraftChange(name, "importance", event.target.value)}
                                                      className="h-8 rounded-lg border border-white/15 bg-ink-900/70 px-2 text-xs"
                                                  >
                                                      <option value="major">主要</option>
                                                      <option value="minor">次要</option>
                                                  </select>
                                              </div>
                                              <textarea
                                                  value=${draft.description}
                                                  onChange=${(event) => onClueDraftChange(name, "description", event.target.value)}
                                                  className="w-full min-h-16 rounded-xl border border-white/15 bg-ink-900/70 px-2 py-2 text-xs"
                                                  placeholder="线索描述"
                                              ></textarea>
                                          </div>
                                      </div>

                                      <div className="flex flex-wrap gap-2">
                                          <${Button} size="sm" variant="outline" onClick=${() => onSaveClue(name)} disabled=${busy}>保存<//>
                                          <${Button} size="sm" onClick=${() => onGenerateClue(name)} disabled=${busy}>生成<//>
                                          <${Button} size="sm" variant="danger" onClick=${() => onDeleteClue(name)} disabled=${busy}>删除<//>
                                          <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                                              上传
                                              <input
                                                  type="file"
                                                  accept="image/*"
                                                  className="hidden"
                                                  onChange=${(event) => {
                                                      const file = event.target.files?.[0];
                                                      if (file) {
                                                          onUploadClueImage(name, file);
                                                      }
                                                      event.target.value = "";
                                                  }}
                                              />
                                          </label>
                                      </div>
                                  </article>
                              `;
                          })}
                      </div>
                  `}
        </div>
    `;
}

function ProjectEpisodes({
    currentProjectData,
    currentProjectName,
    currentScripts,
    draftsByEpisode,
    itemDrafts,
    uploadedStoryboardMap,
    onItemDraftChange,
    onOpenDraftEditor,
    onSaveItem,
    onGenerateStoryboard,
    onGenerateVideo,
    onUploadStoryboard,
    onOpenPreview,
    busy,
}) {
    if (!currentProjectData) {
        return html`
            <${EmptyState}
                title="暂无剧集数据"
                description="请选择项目后查看剧集/场景。"
            />
        `;
    }

    const episodes = currentProjectData.episodes || [];

    if (episodes.length === 0) {
        return html`
            <${EmptyState}
                title="还没有剧集"
                description="系统生成剧本后会自动显示剧集与场景。"
            />
        `;
    }

    return html`
        <div className="space-y-4">
            ${episodes.map((episode) => {
                const scriptFile = episode.script_file?.replace("scripts/", "") || "";
                const script = currentScripts[scriptFile] || {};
                const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
                const contentMode = isNarration ? "narration" : "drama";
                const draftSteps = getDraftSteps(contentMode);
                const draftFiles = draftsByEpisode[String(episode.episode)] || [];
                const items = isNarration ? script.segments || [] : script.scenes || [];
                const itemTypeLabel = isNarration ? "片段" : "场景";

                return html`
                    <article key=${`${episode.episode}-${scriptFile}`} className="rounded-xl border border-white/10 bg-white/5 p-4 space-y-4">
                        <div className="flex items-center justify-between gap-3">
                            <div>
                                <h3 className="font-semibold text-lg">E${episode.episode} · ${episode.title || `第 ${episode.episode} 集`}</h3>
                                <p className="text-xs text-slate-400 mt-1">${scriptFile || "无剧本文件"} · ${isNarration ? "说书模式" : "剧集动画"}</p>
                            </div>
                            <${Badge} className="bg-white/10 border border-white/15 text-slate-200">${itemTypeLabel} ${items.length}<//>
                        </div>

                        <div className="flex flex-wrap gap-2">
                            ${draftSteps.map((draftStep) => {
                                const exists = draftFiles.some((item) => Number(item.step) === draftStep.step);
                                return html`
                                    <button
                                        key=${`${episode.episode}-${draftStep.step}`}
                                        onClick=${() => onOpenDraftEditor(episode.episode, draftStep.step, contentMode)}
                                        className=${cn(
                                            "h-7 px-2 rounded-md text-xs border transition-colors",
                                            exists
                                                ? "border-neon-400/40 bg-neon-500/15 text-neon-300"
                                                : "border-white/10 bg-white/5 text-slate-300 hover:border-white/25"
                                        )}
                                    >
                                        ${exists ? "✓" : "○"} Step ${draftStep.step} · ${draftStep.label}
                                    </button>
                                `;
                            })}
                        </div>

                        ${items.length === 0
                            ? html`<p className="text-sm text-slate-400">当前剧本无${itemTypeLabel}数据。</p>`
                            : html`
                                  <div className="space-y-2">
                                      ${items.map((item) => {
                                          const itemId = item.segment_id || item.scene_id;
                                          if (!itemId) {
                                              return null;
                                          }
                                          const draftKey = itemKey(scriptFile, itemId);
                                          const draft = itemDrafts[draftKey] || {
                                              duration: "4",
                                              segmentBreak: false,
                                              imageScene: readImageScene(item.image_prompt),
                                              videoAction: readVideoAction(item.video_prompt),
                                          };

                                          const assets = item.generated_assets || {};
                                          const storyboardPath = assets.storyboard_image || uploadedStoryboardMap[draftKey] || "";
                                          const videoPath = assets.video_clip || "";
                                          const storyboardUrl = resolveFileUrl(currentProjectName, storyboardPath);
                                          const videoUrl = resolveFileUrl(currentProjectName, videoPath);

                                          const characterRefs = isNarration
                                              ? item.characters_in_segment || []
                                              : item.characters_in_scene || [];
                                          const clueRefs = isNarration
                                              ? item.clues_in_segment || []
                                              : item.clues_in_scene || [];
                                          const descriptionText = item.novel_text
                                              || item.dialogue?.text
                                              || item.visual?.description
                                              || "暂无文本描述";

                                          return html`
                                              <div key=${itemId} className="rounded-xl border border-white/10 bg-ink-900/50 p-3 space-y-2">
                                                  <div className="flex items-center justify-between gap-2">
                                                      <div className="flex items-center gap-2 min-w-0">
                                                          <h4 className="font-semibold text-sm">${itemId}</h4>
                                                          <span className="text-[11px] text-slate-400">${draft.duration || item.duration_seconds || 4}s</span>
                                                          <${Badge} className="bg-slate-500/15 border border-slate-400/30 text-slate-200">${assets.status || "pending"}<//>
                                                      </div>
                                                      <div className="flex items-center gap-2 shrink-0">
                                                          <button
                                                              type="button"
                                                              onClick=${() => storyboardUrl && onOpenPreview(storyboardUrl, "image", `${itemId} 分镜`)}
                                                              className="w-20 h-12 rounded border border-white/10 bg-ink-900/80 overflow-hidden"
                                                          >
                                                              ${storyboardUrl
                                                                  ? html`<img src=${storyboardUrl} alt=${`${itemId} storyboard`} className="w-full h-full object-cover" />`
                                                                  : html`<div className="w-full h-full flex items-center justify-center text-[10px] text-slate-500">分镜</div>`}
                                                          </button>
                                                          <button
                                                              type="button"
                                                              onClick=${() => videoUrl && onOpenPreview(videoUrl, "video", `${itemId} 视频`)}
                                                              className="w-20 h-12 rounded border border-white/10 bg-ink-900/80 overflow-hidden"
                                                          >
                                                              ${videoUrl
                                                                  ? html`<video src=${videoUrl} controls className="w-full h-full object-cover"></video>`
                                                                  : html`<div className="w-full h-full flex items-center justify-center text-[10px] text-slate-500">视频</div>`}
                                                          </button>
                                                      </div>
                                                  </div>

                                                  <p className="text-xs text-slate-300 leading-5 line-clamp-2">${descriptionText}</p>

                                                  <div className="flex flex-wrap gap-2">
                                                      <${Button} size="sm" variant="outline" onClick=${() => onSaveItem(scriptFile, itemId, isNarration)} disabled=${busy}>保存<//>
                                                      <${Button} size="sm" onClick=${() => onGenerateStoryboard(scriptFile, itemId)} disabled=${busy}>分镜<//>
                                                      <${Button} size="sm" onClick=${() => onGenerateVideo(scriptFile, itemId)} disabled=${busy}>视频<//>
                                                      <label className="inline-flex h-8 items-center px-3 text-xs rounded-lg border border-white/20 text-slate-200 hover:border-neon-400/60 hover:text-neon-300 cursor-pointer">
                                                          上传
                                                          <input
                                                              type="file"
                                                              accept="image/*"
                                                              className="hidden"
                                                              onChange=${(event) => {
                                                                  const file = event.target.files?.[0];
                                                                  if (file) {
                                                                      onUploadStoryboard(scriptFile, itemId, file);
                                                                  }
                                                                  event.target.value = "";
                                                              }}
                                                          />
                                                      </label>
                                                  </div>

                                                  <details className="rounded-lg border border-white/10 bg-ink-900/50 p-2">
                                                      <summary className="cursor-pointer text-xs text-slate-300">展开编辑</summary>
                                                      <div className="mt-2 space-y-2">
                                                          <div className="grid grid-cols-2 gap-3">
                                                              <label className="text-xs text-slate-300 space-y-1">
                                                                  <span>时长（秒）</span>
                                                                  <input
                                                                      type="number"
                                                                      min="1"
                                                                      value=${draft.duration}
                                                                      onChange=${(event) => onItemDraftChange(scriptFile, itemId, "duration", event.target.value)}
                                                                      className="w-full h-8 rounded-lg border border-white/15 bg-ink-900/70 px-2 text-xs"
                                                                  />
                                                              </label>
                                                              <label className="text-xs text-slate-300 flex items-center gap-2 mt-5">
                                                                  <input
                                                                      type="checkbox"
                                                                      checked=${Boolean(draft.segmentBreak)}
                                                                      onChange=${(event) => onItemDraftChange(scriptFile, itemId, "segmentBreak", event.target.checked)}
                                                                  />
                                                                  <span>段落转场</span>
                                                              </label>
                                                          </div>

                                                          <label className="text-xs text-slate-300 space-y-1 block">
                                                              <span>分镜 Prompt.scene</span>
                                                              <textarea
                                                                  value=${draft.imageScene}
                                                                  onChange=${(event) => onItemDraftChange(scriptFile, itemId, "imageScene", event.target.value)}
                                                                  className="w-full min-h-14 rounded-xl border border-white/15 bg-ink-900/70 px-2 py-2 text-xs"
                                                              ></textarea>
                                                          </label>

                                                          <label className="text-xs text-slate-300 space-y-1 block">
                                                              <span>视频 Prompt.action</span>
                                                              <textarea
                                                                  value=${draft.videoAction}
                                                                  onChange=${(event) => onItemDraftChange(scriptFile, itemId, "videoAction", event.target.value)}
                                                                  className="w-full min-h-14 rounded-xl border border-white/15 bg-ink-900/70 px-2 py-2 text-xs"
                                                              ></textarea>
                                                          </label>

                                                          <div className="text-xs text-slate-400 space-y-1">
                                                              <div>人物：${characterRefs.length > 0 ? characterRefs.join("、") : "无"}</div>
                                                              <div>线索：${clueRefs.length > 0 ? clueRefs.join("、") : "无"}</div>
                                                          </div>
                                                      </div>
                                                  </details>
                                              </div>
                                          `;
                                      })}
                                  </div>
                              `}
                    </article>
                `;
            })}
        </div>
    `;
}

function WorkspaceTabContent({
    projectTab,
    projectDetailLoading,
    currentProjectData,
    currentProjectName,
    currentScripts,
    projectForm,
    overviewForm,
    styleDescription,
    sourceFiles,
    sourceEditor,
    draftsByEpisode,
    onOpenPreview,
    characterDrafts,
    clueDrafts,
    itemDrafts,
    uploadedStoryboardMap,
    newCharacter,
    newClue,
    handlers,
    busy,
}) {
    if (projectDetailLoading) {
        return html`<div className="py-12 text-center text-slate-400">项目数据加载中...</div>`;
    }

    if (projectTab === "overview") {
        return html`
            <${ProjectOverview}
                currentProjectData=${currentProjectData}
                currentProjectName=${currentProjectName}
                projectForm=${projectForm}
                onProjectFormChange=${handlers.onProjectFormChange}
                onSaveProject=${handlers.onSaveProject}
                overviewForm=${overviewForm}
                onOverviewFormChange=${handlers.onOverviewFormChange}
                onSaveOverview=${handlers.onSaveOverview}
                onGenerateOverview=${handlers.onGenerateOverview}
                styleDescription=${styleDescription}
                onStyleDescriptionChange=${handlers.onStyleDescriptionChange}
                onUploadStyleImage=${handlers.onUploadStyleImage}
                onSaveStyleDescription=${handlers.onSaveStyleDescription}
                onDeleteStyleImage=${handlers.onDeleteStyleImage}
                sourceFiles=${sourceFiles}
                sourceEditor=${sourceEditor}
                onOpenSourceEditor=${handlers.onOpenSourceEditor}
                onSourceEditorChange=${handlers.onSourceEditorChange}
                onCancelSourceEditor=${handlers.onCancelSourceEditor}
                onSaveSourceEditor=${handlers.onSaveSourceEditor}
                onDeleteSourceFile=${handlers.onDeleteSourceFile}
                onUploadSource=${handlers.onUploadSource}
                onOpenPreview=${onOpenPreview}
                busy=${busy}
            />
        `;
    }

    if (projectTab === "tasks") {
        return html`
            <${ProjectTasks}
                currentProjectData=${currentProjectData}
                currentProjectName=${currentProjectName}
                characterDrafts=${characterDrafts}
                newCharacter=${newCharacter}
                onNewCharacterChange=${handlers.onNewCharacterChange}
                onCreateCharacter=${handlers.onCreateCharacter}
                onCharacterDraftChange=${handlers.onCharacterDraftChange}
                onSaveCharacter=${handlers.onSaveCharacter}
                onDeleteCharacter=${handlers.onDeleteCharacter}
                onUploadCharacterImage=${handlers.onUploadCharacterImage}
                onUploadCharacterReference=${handlers.onUploadCharacterReference}
                onGenerateCharacter=${handlers.onGenerateCharacter}
                onOpenPreview=${onOpenPreview}
                busy=${busy}
            />
        `;
    }

    if (projectTab === "clues") {
        return html`
            <${ProjectClues}
                currentProjectData=${currentProjectData}
                currentProjectName=${currentProjectName}
                clueDrafts=${clueDrafts}
                newClue=${newClue}
                onNewClueChange=${handlers.onNewClueChange}
                onCreateClue=${handlers.onCreateClue}
                onClueDraftChange=${handlers.onClueDraftChange}
                onSaveClue=${handlers.onSaveClue}
                onDeleteClue=${handlers.onDeleteClue}
                onUploadClueImage=${handlers.onUploadClueImage}
                onGenerateClue=${handlers.onGenerateClue}
                onOpenPreview=${onOpenPreview}
                busy=${busy}
            />
        `;
    }

    return html`
        <${ProjectEpisodes}
            currentProjectData=${currentProjectData}
            currentProjectName=${currentProjectName}
            currentScripts=${currentScripts}
            draftsByEpisode=${draftsByEpisode}
            itemDrafts=${itemDrafts}
            uploadedStoryboardMap=${uploadedStoryboardMap}
            onItemDraftChange=${handlers.onItemDraftChange}
            onOpenDraftEditor=${handlers.onOpenDraftEditor}
            onSaveItem=${handlers.onSaveItem}
            onGenerateStoryboard=${handlers.onGenerateStoryboard}
            onGenerateVideo=${handlers.onGenerateVideo}
            onUploadStoryboard=${handlers.onUploadStoryboard}
            onOpenPreview=${onOpenPreview}
            busy=${busy}
        />
    `;
}

export function WorkspacePage({
    currentProjectData,
    currentProjectName,
    projectTab,
    onChangeProjectTab,
    onRefreshProject,
    onDeleteProject,
    projectDetailLoading,
    currentScripts,
    pushToast,
}) {
    const [busyKey, setBusyKey] = useState("");
    const [projectForm, setProjectForm] = useState(DEFAULT_PROJECT_FORM);
    const [overviewForm, setOverviewForm] = useState(DEFAULT_OVERVIEW_FORM);
    const [styleDescription, setStyleDescription] = useState("");
    const [sourceFiles, setSourceFiles] = useState([]);
    const [sourceEditor, setSourceEditor] = useState(DEFAULT_SOURCE_EDITOR);
    const [draftsByEpisode, setDraftsByEpisode] = useState({});
    const [draftEditor, setDraftEditor] = useState(DEFAULT_DRAFT_EDITOR);
    const [previewMedia, setPreviewMedia] = useState(DEFAULT_PREVIEW_MEDIA);
    const [newCharacter, setNewCharacter] = useState(DEFAULT_CHARACTER_FORM);
    const [newClue, setNewClue] = useState(DEFAULT_CLUE_FORM);
    const [characterDrafts, setCharacterDrafts] = useState({});
    const [clueDrafts, setClueDrafts] = useState({});
    const [itemDrafts, setItemDrafts] = useState({});
    const [uploadedStoryboardMap, setUploadedStoryboardMap] = useState({});

    useEffect(() => {
        setProjectForm(createProjectForm(currentProjectData));
        setOverviewForm(createOverviewForm(currentProjectData));
        setStyleDescription(currentProjectData?.style_description || "");
    }, [currentProjectData]);

    useEffect(() => {
        setCharacterDrafts(createCharacterDrafts(currentProjectData?.characters || {}));
    }, [currentProjectData]);

    useEffect(() => {
        setClueDrafts(createClueDrafts(currentProjectData?.clues || {}));
    }, [currentProjectData]);

    useEffect(() => {
        setItemDrafts(createItemDrafts(currentScripts || {}));
    }, [currentScripts]);

    useEffect(() => {
        setUploadedStoryboardMap({});
        setSourceEditor(DEFAULT_SOURCE_EDITOR);
        setDraftEditor(DEFAULT_DRAFT_EDITOR);
        setPreviewMedia(DEFAULT_PREVIEW_MEDIA);
    }, [currentProjectName]);

    const notify = useCallback(
        (message, tone = "info") => {
            if (typeof pushToast === "function") {
                pushToast(message, tone);
            }
        },
        [pushToast]
    );

    const loadSourceFiles = useCallback(async () => {
        if (!currentProjectName || !window.API) {
            setSourceFiles([]);
            return;
        }
        try {
            const data = await window.API.listFiles(currentProjectName);
            setSourceFiles(data?.files?.source || []);
        } catch (_error) {
            setSourceFiles([]);
        }
    }, [currentProjectName]);

    useEffect(() => {
        void loadSourceFiles();
    }, [loadSourceFiles]);

    const loadDrafts = useCallback(async () => {
        if (!currentProjectName || !window.API) {
            setDraftsByEpisode({});
            return;
        }
        try {
            const data = await window.API.listDrafts(currentProjectName);
            setDraftsByEpisode(data?.drafts || {});
        } catch (_error) {
            setDraftsByEpisode({});
        }
    }, [currentProjectName]);

    useEffect(() => {
        void loadDrafts();
    }, [loadDrafts]);

    const runAction = useCallback(
        async (key, action, options = {}) => {
            if (!currentProjectName) {
                notify("请先选择项目", "error");
                return false;
            }
            if (!window.API) {
                notify("API 未初始化", "error");
                return false;
            }

            setBusyKey(key);
            try {
                const result = await action(window.API);
                if (typeof options.onSuccess === "function") {
                    options.onSuccess(result);
                }
                if (options.refreshProject !== false && typeof onRefreshProject === "function") {
                    await onRefreshProject();
                }
                if (options.successText) {
                    notify(options.successText, "success");
                }
                return true;
            } catch (error) {
                const prefix = options.errorPrefix || "操作失败";
                notify(`${prefix}：${error.message || "未知错误"}`, "error");
                return false;
            } finally {
                setBusyKey("");
            }
        },
        [currentProjectName, notify, onRefreshProject]
    );

    const onProjectFormChange = useCallback((field, value) => {
        setProjectForm((prev) => ({ ...prev, [field]: value }));
    }, []);

    const onSaveProject = useCallback(async () => {
        const title = String(projectForm.title || "").trim();
        if (!title) {
            notify("项目标题不能为空", "error");
            return;
        }

        await runAction(
            "save-project-meta",
            (api) => api.updateProject(currentProjectName, {
                title,
                style: projectForm.style,
                content_mode: projectForm.contentMode,
            }),
            {
                successText: "项目信息已保存",
                errorPrefix: "保存项目信息失败",
            }
        );
    }, [currentProjectName, notify, projectForm, runAction]);

    const onOverviewFormChange = useCallback((field, value) => {
        setOverviewForm((prev) => ({ ...prev, [field]: value }));
    }, []);

    const onSaveOverview = useCallback(async () => {
        await runAction(
            "save-overview",
            (api) => api.updateOverview(currentProjectName, {
                synopsis: overviewForm.synopsis,
                genre: overviewForm.genre,
                theme: overviewForm.theme,
                world_setting: overviewForm.worldSetting,
            }),
            {
                successText: "剧情概述已保存",
                errorPrefix: "保存剧情概述失败",
            }
        );
    }, [currentProjectName, overviewForm, runAction]);

    const onGenerateOverview = useCallback(async () => {
        await runAction(
            "generate-overview",
            (api) => api.generateOverview(currentProjectName),
            {
                successText: "剧情概述已重新生成",
                errorPrefix: "生成剧情概述失败",
                onSuccess: (result) => {
                    if (result?.overview) {
                        setOverviewForm({
                            synopsis: result.overview.synopsis || "",
                            genre: result.overview.genre || "",
                            theme: result.overview.theme || "",
                            worldSetting: result.overview.world_setting || "",
                        });
                    }
                },
            }
        );
    }, [currentProjectName, runAction]);

    const onStyleDescriptionChange = useCallback((value) => {
        setStyleDescription(value);
    }, []);

    const onUploadStyleImage = useCallback(
        async (file) => {
            await runAction(
                "upload-style-image",
                (api) => api.uploadStyleImage(currentProjectName, file),
                {
                    successText: "风格参考图已上传",
                    errorPrefix: "上传风格图失败",
                    onSuccess: (result) => {
                        if (result?.style_description) {
                            setStyleDescription(result.style_description);
                        }
                    },
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onSaveStyleDescription = useCallback(async () => {
        await runAction(
            "save-style-description",
            (api) => api.updateStyleDescription(currentProjectName, styleDescription),
            {
                successText: "风格描述已保存",
                errorPrefix: "保存风格描述失败",
                refreshProject: false,
            }
        );
    }, [currentProjectName, runAction, styleDescription]);

    const onDeleteStyleImage = useCallback(async () => {
        if (!window.confirm("确定删除风格参考图吗？")) {
            return;
        }
        await runAction(
            "delete-style-image",
            (api) => api.deleteStyleImage(currentProjectName),
            {
                successText: "风格参考图已删除",
                errorPrefix: "删除风格图失败",
                onSuccess: () => setStyleDescription(""),
            }
        );
    }, [currentProjectName, runAction]);

    const onSourceEditorChange = useCallback((field, value) => {
        setSourceEditor((prev) => ({ ...prev, [field]: value }));
    }, []);

    const onCancelSourceEditor = useCallback(() => {
        setSourceEditor(DEFAULT_SOURCE_EDITOR);
    }, []);

    const onOpenSourceEditor = useCallback(
        async (filename) => {
            if (!filename) {
                setSourceEditor({
                    open: true,
                    filename: "",
                    content: "",
                    editingExisting: false,
                });
                return;
            }

            await runAction(
                `open-source-${filename}`,
                (api) => api.getSourceContent(currentProjectName, filename),
                {
                    refreshProject: false,
                    errorPrefix: "加载源文件失败",
                    onSuccess: (content) => {
                        setSourceEditor({
                            open: true,
                            filename,
                            content: String(content || ""),
                            editingExisting: true,
                        });
                    },
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onSaveSourceEditor = useCallback(async () => {
        const filenameRaw = String(sourceEditor.filename || "").trim();
        if (!filenameRaw) {
            notify("文件名不能为空", "error");
            return;
        }

        const filename = filenameRaw.endsWith(".txt") || filenameRaw.endsWith(".md")
            ? filenameRaw
            : `${filenameRaw}.txt`;

        await runAction(
            "save-source-file",
            (api) => api.saveSourceFile(currentProjectName, filename, sourceEditor.content || ""),
            {
                refreshProject: false,
                successText: `${filename} 已保存`,
                errorPrefix: "保存源文件失败",
                onSuccess: () => {
                    setSourceEditor(DEFAULT_SOURCE_EDITOR);
                    void loadSourceFiles();
                },
            }
        );
    }, [currentProjectName, loadSourceFiles, notify, runAction, sourceEditor]);

    const onDeleteSourceFile = useCallback(
        async (filename) => {
            if (!window.confirm(`确定删除文件 "${filename}" 吗？`)) {
                return;
            }

            await runAction(
                `delete-source-${filename}`,
                (api) => api.deleteSourceFile(currentProjectName, filename),
                {
                    refreshProject: false,
                    successText: `${filename} 已删除`,
                    errorPrefix: "删除源文件失败",
                    onSuccess: () => {
                        if (sourceEditor.open && sourceEditor.filename === filename) {
                            setSourceEditor(DEFAULT_SOURCE_EDITOR);
                        }
                        void loadSourceFiles();
                    },
                }
            );
        },
        [currentProjectName, loadSourceFiles, runAction, sourceEditor]
    );

    const onUploadSource = useCallback(
        async (file) => {
            await runAction(
                "upload-source",
                (api) => api.uploadFile(currentProjectName, "source", file),
                {
                    refreshProject: false,
                    successText: `${file.name} 已上传`,
                    errorPrefix: "上传源文件失败",
                    onSuccess: () => {
                        void loadSourceFiles();
                    },
                }
            );
        },
        [currentProjectName, loadSourceFiles, runAction]
    );

    const onOpenPreview = useCallback((url, type = "image", title = "") => {
        if (!url) {
            return;
        }
        setPreviewMedia({
            open: true,
            type,
            url,
            title,
        });
    }, []);

    const onClosePreview = useCallback(() => {
        setPreviewMedia(DEFAULT_PREVIEW_MEDIA);
    }, []);

    const onOpenDraftEditor = useCallback(
        async (episode, step, contentMode) => {
            if (!currentProjectName || !window.API) {
                return;
            }

            setBusyKey(`open-draft-${episode}-${step}`);
            try {
                let content = "";
                try {
                    content = await window.API.getDraftContent(currentProjectName, episode, step);
                } catch (_error) {
                    content = "";
                }

                setDraftEditor({
                    open: true,
                    episode: String(episode),
                    step: Number(step),
                    content,
                    contentMode: contentMode || "narration",
                });
            } finally {
                setBusyKey("");
            }
        },
        [currentProjectName]
    );

    const onCloseDraftEditor = useCallback(() => {
        setDraftEditor(DEFAULT_DRAFT_EDITOR);
    }, []);

    const onDraftContentChange = useCallback((value) => {
        setDraftEditor((prev) => ({ ...prev, content: value }));
    }, []);

    const onSaveDraft = useCallback(async () => {
        if (!draftEditor.open) {
            return;
        }

        await runAction(
            `save-draft-${draftEditor.episode}-${draftEditor.step}`,
            (api) => api.saveDraft(
                currentProjectName,
                Number(draftEditor.episode),
                Number(draftEditor.step),
                draftEditor.content || ""
            ),
            {
                refreshProject: false,
                successText: `Step ${draftEditor.step} 草稿已保存`,
                errorPrefix: "保存草稿失败",
                onSuccess: () => {
                    void loadDrafts();
                    setDraftEditor(DEFAULT_DRAFT_EDITOR);
                },
            }
        );
    }, [currentProjectName, draftEditor, loadDrafts, runAction]);

    const onNewCharacterChange = useCallback((field, value) => {
        setNewCharacter((prev) => ({ ...prev, [field]: value }));
    }, []);

    const onCreateCharacter = useCallback(async () => {
        const name = newCharacter.name.trim();
        const description = newCharacter.description.trim();

        if (!name || !description) {
            notify("新增人物需要名称和描述", "error");
            return;
        }

        const ok = await runAction(
            "create-character",
            (api) => api.addCharacter(currentProjectName, name, description, newCharacter.voiceStyle.trim()),
            {
                successText: `人物 ${name} 已创建`,
                errorPrefix: "创建人物失败",
            }
        );

        if (ok) {
            setNewCharacter(DEFAULT_CHARACTER_FORM);
        }
    }, [currentProjectName, newCharacter, notify, runAction]);

    const onCharacterDraftChange = useCallback((name, field, value) => {
        setCharacterDrafts((prev) => ({
            ...prev,
            [name]: {
                ...(prev[name] || { description: "", voiceStyle: "" }),
                [field]: value,
            },
        }));
    }, []);

    const onSaveCharacter = useCallback(
        async (name) => {
            const draft = characterDrafts[name] || { description: "", voiceStyle: "" };
            const description = String(draft.description || "").trim();
            const voiceStyle = String(draft.voiceStyle || "").trim();

            if (!description) {
                notify("人物描述不能为空", "error");
                return;
            }

            await runAction(
                `save-character-${name}`,
                (api) => api.updateCharacter(currentProjectName, name, {
                    description,
                    voice_style: voiceStyle,
                }),
                {
                    successText: `人物 ${name} 已保存`,
                    errorPrefix: "保存人物失败",
                }
            );
        },
        [characterDrafts, currentProjectName, notify, runAction]
    );

    const onDeleteCharacter = useCallback(
        async (name) => {
            if (!window.confirm(`确定删除人物 "${name}" 吗？`)) {
                return;
            }
            await runAction(
                `delete-character-${name}`,
                (api) => api.deleteCharacter(currentProjectName, name),
                {
                    successText: `人物 ${name} 已删除`,
                    errorPrefix: "删除人物失败",
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onUploadCharacterImage = useCallback(
        async (name, file) => {
            await runAction(
                `upload-character-${name}`,
                (api) => api.uploadFile(currentProjectName, "character", file, name),
                {
                    successText: `人物 ${name} 图片已上传`,
                    errorPrefix: "上传人物图失败",
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onUploadCharacterReference = useCallback(
        async (name, file) => {
            await runAction(
                `upload-character-ref-${name}`,
                (api) => api.uploadFile(currentProjectName, "character_ref", file, name),
                {
                    successText: `人物 ${name} 参考图已上传`,
                    errorPrefix: "上传参考图失败",
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onGenerateCharacter = useCallback(
        async (name) => {
            const draft = characterDrafts[name] || {};
            const fallbackDescription = currentProjectData?.characters?.[name]?.description || "";
            const prompt = String(draft.description || fallbackDescription).trim();

            if (!prompt) {
                notify(`人物 ${name} 缺少描述，无法生成`, "error");
                return;
            }

            await runAction(
                `generate-character-${name}`,
                (api) => api.generateCharacter(currentProjectName, name, prompt),
                {
                    successText: `人物 ${name} 生成完成`,
                    errorPrefix: "生成人物图失败",
                }
            );
        },
        [characterDrafts, currentProjectData, currentProjectName, notify, runAction]
    );

    const onNewClueChange = useCallback((field, value) => {
        setNewClue((prev) => ({ ...prev, [field]: value }));
    }, []);

    const onCreateClue = useCallback(async () => {
        const name = newClue.name.trim();
        const description = newClue.description.trim();

        if (!name || !description) {
            notify("新增线索需要名称和描述", "error");
            return;
        }

        const ok = await runAction(
            "create-clue",
            (api) => api.addClue(currentProjectName, name, newClue.clueType, description, newClue.importance),
            {
                successText: `线索 ${name} 已创建`,
                errorPrefix: "创建线索失败",
            }
        );

        if (ok) {
            setNewClue(DEFAULT_CLUE_FORM);
        }
    }, [currentProjectName, newClue, notify, runAction]);

    const onClueDraftChange = useCallback((name, field, value) => {
        setClueDrafts((prev) => ({
            ...prev,
            [name]: {
                ...(prev[name] || { clueType: "prop", importance: "major", description: "" }),
                [field]: value,
            },
        }));
    }, []);

    const onSaveClue = useCallback(
        async (name) => {
            const draft = clueDrafts[name] || {};
            const description = String(draft.description || "").trim();
            if (!description) {
                notify("线索描述不能为空", "error");
                return;
            }

            await runAction(
                `save-clue-${name}`,
                (api) => api.updateClue(currentProjectName, name, {
                    clue_type: draft.clueType || "prop",
                    importance: draft.importance || "major",
                    description,
                }),
                {
                    successText: `线索 ${name} 已保存`,
                    errorPrefix: "保存线索失败",
                }
            );
        },
        [clueDrafts, currentProjectName, notify, runAction]
    );

    const onDeleteClue = useCallback(
        async (name) => {
            if (!window.confirm(`确定删除线索 "${name}" 吗？`)) {
                return;
            }
            await runAction(
                `delete-clue-${name}`,
                (api) => api.deleteClue(currentProjectName, name),
                {
                    successText: `线索 ${name} 已删除`,
                    errorPrefix: "删除线索失败",
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onUploadClueImage = useCallback(
        async (name, file) => {
            await runAction(
                `upload-clue-${name}`,
                (api) => api.uploadFile(currentProjectName, "clue", file, name),
                {
                    successText: `线索 ${name} 图片已上传`,
                    errorPrefix: "上传线索图失败",
                }
            );
        },
        [currentProjectName, runAction]
    );

    const onGenerateClue = useCallback(
        async (name) => {
            const draft = clueDrafts[name] || {};
            const fallbackDescription = currentProjectData?.clues?.[name]?.description || "";
            const prompt = String(draft.description || fallbackDescription).trim();

            if (!prompt) {
                notify(`线索 ${name} 缺少描述，无法生成`, "error");
                return;
            }

            await runAction(
                `generate-clue-${name}`,
                (api) => api.generateClue(currentProjectName, name, prompt),
                {
                    successText: `线索 ${name} 生成完成`,
                    errorPrefix: "生成线索图失败",
                }
            );
        },
        [clueDrafts, currentProjectData, currentProjectName, notify, runAction]
    );

    const onItemDraftChange = useCallback((scriptFile, itemId, field, value) => {
        const key = itemKey(scriptFile, itemId);
        setItemDrafts((prev) => ({
            ...prev,
            [key]: {
                ...(prev[key] || {
                    duration: "4",
                    segmentBreak: false,
                    imageScene: "",
                    videoAction: "",
                }),
                [field]: value,
            },
        }));
    }, []);

    const onSaveItem = useCallback(
        async (scriptFile, itemId, isNarration) => {
            const script = currentScripts?.[scriptFile];
            if (!script) {
                notify("找不到对应剧本，无法保存", "error");
                return;
            }

            const items = isNarration ? script.segments || [] : script.scenes || [];
            const idField = isNarration ? "segment_id" : "scene_id";
            const item = items.find((entry) => entry[idField] === itemId);
            if (!item) {
                notify("找不到对应片段/场景，无法保存", "error");
                return;
            }

            const draft = itemDrafts[itemKey(scriptFile, itemId)] || {
                duration: String(item.duration_seconds || 4),
                segmentBreak: Boolean(item.segment_break),
                imageScene: readImageScene(item.image_prompt),
                videoAction: readVideoAction(item.video_prompt),
            };

            const duration = Number.parseInt(draft.duration, 10);
            if (!Number.isFinite(duration) || duration <= 0) {
                notify("时长必须是正整数", "error");
                return;
            }

            const imagePrompt = normalizeImagePrompt(item.image_prompt, draft.imageScene);
            const videoPrompt = normalizeVideoPrompt(item.video_prompt, draft.videoAction);

            if (isNarration) {
                await runAction(
                    `save-segment-${itemId}`,
                    (api) => api.updateSegment(currentProjectName, itemId, {
                        script_file: scriptFile,
                        duration_seconds: duration,
                        segment_break: Boolean(draft.segmentBreak),
                        image_prompt: imagePrompt,
                        video_prompt: videoPrompt,
                    }),
                    {
                        successText: `${itemId} 已保存`,
                        errorPrefix: "保存片段失败",
                    }
                );
                return;
            }

            await runAction(
                `save-scene-${itemId}`,
                (api) => api.updateScene(currentProjectName, itemId, scriptFile, {
                    duration_seconds: duration,
                    segment_break: Boolean(draft.segmentBreak),
                    image_prompt: imagePrompt,
                    video_prompt: videoPrompt,
                }),
                {
                    successText: `${itemId} 已保存`,
                    errorPrefix: "保存场景失败",
                }
            );
        },
        [currentProjectName, currentScripts, itemDrafts, notify, runAction]
    );

    const onGenerateStoryboard = useCallback(
        async (scriptFile, itemId) => {
            const script = currentScripts?.[scriptFile];
            if (!script) {
                notify("找不到对应剧本，无法生成分镜", "error");
                return;
            }

            const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
            const items = isNarration ? script.segments || [] : script.scenes || [];
            const idField = isNarration ? "segment_id" : "scene_id";
            const item = items.find((entry) => entry[idField] === itemId);
            if (!item) {
                notify("找不到对应片段/场景，无法生成分镜", "error");
                return;
            }

            const draft = itemDrafts[itemKey(scriptFile, itemId)] || {};
            const prompt = normalizeImagePrompt(item.image_prompt, draft.imageScene);
            if (!String(prompt.scene || "").trim()) {
                notify("分镜 prompt.scene 不能为空", "error");
                return;
            }

            await runAction(
                `generate-storyboard-${itemId}`,
                (api) => api.generateStoryboard(currentProjectName, itemId, prompt, scriptFile),
                {
                    successText: `${itemId} 分镜生成完成`,
                    errorPrefix: "生成分镜失败",
                }
            );
        },
        [currentProjectName, currentScripts, itemDrafts, notify, runAction]
    );

    const onGenerateVideo = useCallback(
        async (scriptFile, itemId) => {
            const script = currentScripts?.[scriptFile];
            if (!script) {
                notify("找不到对应剧本，无法生成视频", "error");
                return;
            }

            const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
            const items = isNarration ? script.segments || [] : script.scenes || [];
            const idField = isNarration ? "segment_id" : "scene_id";
            const item = items.find((entry) => entry[idField] === itemId);
            if (!item) {
                notify("找不到对应片段/场景，无法生成视频", "error");
                return;
            }

            const draft = itemDrafts[itemKey(scriptFile, itemId)] || {};
            const prompt = normalizeVideoPrompt(item.video_prompt, draft.videoAction);
            if (!String(prompt.action || "").trim()) {
                notify("视频 prompt.action 不能为空", "error");
                return;
            }

            const duration = Number.parseInt(draft.duration || item.duration_seconds || "4", 10);
            if (!Number.isFinite(duration) || duration <= 0) {
                notify("时长必须是正整数", "error");
                return;
            }

            await runAction(
                `generate-video-${itemId}`,
                (api) => api.generateVideo(currentProjectName, itemId, prompt, scriptFile, duration),
                {
                    successText: `${itemId} 视频生成完成`,
                    errorPrefix: "生成视频失败",
                }
            );
        },
        [currentProjectName, currentScripts, itemDrafts, notify, runAction]
    );

    const onUploadStoryboard = useCallback(
        async (scriptFile, itemId, file) => {
            const key = itemKey(scriptFile, itemId);
            await runAction(
                `upload-storyboard-${itemId}`,
                (api) => api.uploadFile(currentProjectName, "storyboard", file, itemId),
                {
                    successText: `${itemId} 分镜素材已上传`,
                    errorPrefix: "上传分镜素材失败",
                    onSuccess: (result) => {
                        if (result?.path) {
                            setUploadedStoryboardMap((prev) => ({
                                ...prev,
                                [key]: result.path,
                            }));
                        }
                    },
                }
            );
        },
        [currentProjectName, runAction]
    );

    const handlers = useMemo(
        () => ({
            onProjectFormChange,
            onSaveProject,
            onOverviewFormChange,
            onSaveOverview,
            onGenerateOverview,
            onStyleDescriptionChange,
            onUploadStyleImage,
            onSaveStyleDescription,
            onDeleteStyleImage,
            onOpenSourceEditor,
            onSourceEditorChange,
            onCancelSourceEditor,
            onSaveSourceEditor,
            onDeleteSourceFile,
            onUploadSource,
            onOpenDraftEditor,
            onCloseDraftEditor,
            onDraftContentChange,
            onSaveDraft,
            onClosePreview,
            onNewCharacterChange,
            onCreateCharacter,
            onCharacterDraftChange,
            onSaveCharacter,
            onDeleteCharacter,
            onUploadCharacterImage,
            onUploadCharacterReference,
            onGenerateCharacter,
            onNewClueChange,
            onCreateClue,
            onClueDraftChange,
            onSaveClue,
            onDeleteClue,
            onUploadClueImage,
            onGenerateClue,
            onItemDraftChange,
            onSaveItem,
            onGenerateStoryboard,
            onGenerateVideo,
            onUploadStoryboard,
        }),
        [
            onProjectFormChange,
            onSaveProject,
            onOverviewFormChange,
            onSaveOverview,
            onGenerateOverview,
            onStyleDescriptionChange,
            onUploadStyleImage,
            onSaveStyleDescription,
            onDeleteStyleImage,
            onOpenSourceEditor,
            onSourceEditorChange,
            onCancelSourceEditor,
            onSaveSourceEditor,
            onDeleteSourceFile,
            onUploadSource,
            onOpenDraftEditor,
            onCloseDraftEditor,
            onDraftContentChange,
            onSaveDraft,
            onClosePreview,
            onNewCharacterChange,
            onCreateCharacter,
            onCharacterDraftChange,
            onSaveCharacter,
            onDeleteCharacter,
            onUploadCharacterImage,
            onUploadCharacterReference,
            onGenerateCharacter,
            onNewClueChange,
            onCreateClue,
            onClueDraftChange,
            onSaveClue,
            onDeleteClue,
            onUploadClueImage,
            onGenerateClue,
            onItemDraftChange,
            onSaveItem,
            onGenerateStoryboard,
            onGenerateVideo,
            onUploadStoryboard,
        ]
    );

    const busy = Boolean(busyKey);

    return html`
        <div className="h-full min-h-0">
            <${Card} className="h-full min-h-0 flex flex-col gap-2 overflow-hidden">
                <header className="flex items-center justify-between gap-2 shrink-0">
                    <h2 className="text-sm font-semibold app-title truncate">${currentProjectData?.title || currentProjectName || "项目"}</h2>
                    <div className="flex items-center gap-2">
                        <${Button} size="sm" variant="outline" onClick=${onRefreshProject} disabled=${busy}>刷新项目<//>
                        <${Button} size="sm" variant="danger" onClick=${onDeleteProject} disabled=${busy}>删除项目<//>
                    </div>
                </header>

                <nav className="grid grid-cols-2 md:grid-cols-4 gap-2 shrink-0">
                    ${PROJECT_TABS.map((tab) => html`
                        <button
                            key=${tab.key}
                            onClick=${() => onChangeProjectTab(tab.key)}
                            className=${cn(
                                "h-8 rounded-lg text-xs transition-colors",
                                projectTab === tab.key
                                    ? "bg-neon-500/20 text-neon-300 border border-neon-400/30"
                                    : "bg-white/5 border border-white/10 text-slate-300 hover:border-white/25"
                            )}
                        >
                            ${tab.label}
                        </button>
                    `)}
                </nav>

                <div className="flex-1 min-h-0 overflow-y-auto pr-1">
                    <${WorkspaceTabContent}
                        projectTab=${projectTab}
                        projectDetailLoading=${projectDetailLoading}
                        currentProjectData=${currentProjectData}
                        currentProjectName=${currentProjectName}
                        currentScripts=${currentScripts}
                        projectForm=${projectForm}
                        overviewForm=${overviewForm}
                        styleDescription=${styleDescription}
                        sourceFiles=${sourceFiles}
                        sourceEditor=${sourceEditor}
                        draftsByEpisode=${draftsByEpisode}
                        onOpenPreview=${onOpenPreview}
                        characterDrafts=${characterDrafts}
                        clueDrafts=${clueDrafts}
                        itemDrafts=${itemDrafts}
                        uploadedStoryboardMap=${uploadedStoryboardMap}
                        newCharacter=${newCharacter}
                        newClue=${newClue}
                        handlers=${handlers}
                        busy=${busy}
                    />
                </div>
            <//>

            ${draftEditor.open
                ? html`
                      <div className="fixed inset-0 z-40 bg-black/60 flex items-center justify-center p-4">
                          <div className="w-full max-w-4xl rounded-2xl border border-white/15 bg-ink-900 p-4 space-y-3">
                              <div className="flex items-center justify-between gap-3">
                                  <h3 className="text-base font-semibold">
                                      草稿编辑 · 第 ${draftEditor.episode} 集 · Step ${draftEditor.step}
                                  </h3>
                                  <button
                                      onClick=${handlers.onCloseDraftEditor}
                                      className="h-8 px-3 rounded-lg text-xs border border-white/20 text-slate-200 hover:border-white/40"
                                  >
                                      关闭
                                  </button>
                              </div>

                              <textarea
                                  value=${draftEditor.content}
                                  onChange=${(event) => handlers.onDraftContentChange(event.target.value)}
                                  className="w-full min-h-[360px] rounded-xl border border-white/15 bg-ink-950/70 px-3 py-2 text-sm"
                              ></textarea>

                              <div className="flex items-center gap-2">
                                  <${Button} size="sm" onClick=${handlers.onSaveDraft} disabled=${busy}>保存草稿<//>
                                  <${Button} size="sm" variant="ghost" onClick=${handlers.onCloseDraftEditor} disabled=${busy}>取消<//>
                              </div>
                          </div>
                      </div>
                  `
                : null}

            ${previewMedia.open
                ? html`
                      <div className="fixed inset-0 z-30 bg-black/70 flex items-center justify-center p-4" onClick=${handlers.onClosePreview}>
                          <div className="w-full max-w-5xl rounded-2xl border border-white/15 bg-ink-900 p-3 space-y-2" onClick=${(event) => event.stopPropagation()}>
                              <div className="flex items-center justify-between gap-3">
                                  <h3 className="text-sm font-semibold text-slate-200">${previewMedia.title || "预览"}</h3>
                                  <button
                                      onClick=${handlers.onClosePreview}
                                      className="h-8 px-3 rounded-lg text-xs border border-white/20 text-slate-200 hover:border-white/40"
                                  >
                                      关闭
                                  </button>
                              </div>
                              <div className="rounded-xl border border-white/10 bg-ink-950/60 p-2 max-h-[72vh] overflow-auto">
                                  ${previewMedia.type === "video"
                                      ? html`<video src=${previewMedia.url} controls className="w-full max-h-[68vh] object-contain bg-black rounded-lg"></video>`
                                      : html`<img src=${previewMedia.url} alt=${previewMedia.title || "preview"} className="w-full max-h-[68vh] object-contain rounded-lg" />`}
                              </div>
                          </div>
                      </div>
                  `
                : null}
        </div>
    `;
}
