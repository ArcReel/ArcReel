function itemKey(scriptFile, itemId) {
    return `${scriptFile}::${itemId}`;
}

export function buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    if (!selectedReview?.scriptFile || !selectedReview?.itemId) {
        return null;
    }

    const script = currentScripts?.[selectedReview.scriptFile];
    if (!script) {
        return null;
    }

    const isNarration = script.content_mode === "narration" && Array.isArray(script.segments);
    const items = isNarration ? script.segments || [] : script.scenes || [];
    const idField = isNarration ? "segment_id" : "scene_id";
    const item = items.find((entry) => entry[idField] === selectedReview.itemId);
    if (!item) {
        return null;
    }

    const assets = item.generated_assets || {};
    const key = itemKey(selectedReview.scriptFile, selectedReview.itemId);

    return {
        scriptFile: selectedReview.scriptFile,
        itemId: selectedReview.itemId,
        item,
        isNarration,
        videoPath: assets.video_clip || "",
        storyboardPath: assets.storyboard_image || uploadedStoryboardMap[key] || "",
        status: assets.status || "pending",
        duration: item.duration_seconds || 4,
    };
}

export function getSafeReviewSelection(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    const target = buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap);
    if (!target?.videoPath) {
        return null;
    }
    return selectedReview;
}

export function getReviewSelectionResult(currentScripts, selectedReview, uploadedStoryboardMap = {}) {
    const target = buildReviewTargetFromSelection(currentScripts, selectedReview, uploadedStoryboardMap);
    if (!target) {
        return { ok: false, error: "找不到对应片段/场景", target: null };
    }
    if (!target.videoPath) {
        return { ok: false, error: "该场景暂无可播放视频", target };
    }
    return { ok: true, error: "", target };
}

export function normalizeReviewMediaError(message) {
    if (message === null || message === undefined) {
        return "视频加载失败";
    }
    return String(message).trim();
}

export function buildReviewVideoUrl(videoUrl, mediaVersion = 0) {
    if (!videoUrl) {
        return "";
    }
    if (!mediaVersion || mediaVersion <= 0) {
        return videoUrl;
    }
    const separator = String(videoUrl).includes("?") ? "&" : "?";
    return `${videoUrl}${separator}rev=${mediaVersion}`;
}

export function isReviewItemSelected(selectedReview, scriptFile, itemId) {
    if (!selectedReview) {
        return false;
    }
    return selectedReview.scriptFile === scriptFile && selectedReview.itemId === itemId;
}

export function getReviewMediaVersionForSelection(reviewMediaVersions, selectedReview) {
    if (!selectedReview?.scriptFile || !selectedReview?.itemId) {
        return 0;
    }
    const key = itemKey(selectedReview.scriptFile, selectedReview.itemId);
    const value = Number(reviewMediaVersions?.[key] || 0);
    if (!Number.isFinite(value) || value <= 0) {
        return 0;
    }
    return value;
}

export function bumpReviewMediaVersionForItem(reviewMediaVersions, scriptFile, itemId) {
    if (!scriptFile || !itemId) {
        return reviewMediaVersions || {};
    }
    const key = itemKey(scriptFile, itemId);
    const base = reviewMediaVersions || {};
    const current = Number(base[key] || 0);
    const nextValue = Number.isFinite(current) && current > 0 ? current + 1 : 1;
    return {
        ...base,
        [key]: nextValue,
    };
}
