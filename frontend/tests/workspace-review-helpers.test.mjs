import test from "node:test";
import assert from "node:assert/strict";

import {
    buildReviewTargetFromSelection,
    getReviewMediaVersionForSelection,
    getReviewSelectionResult,
    bumpReviewMediaVersionForItem,
} from "../src/react/pages/workspace-review-helpers.js";

const narrationScripts = {
    "episode_2.json": {
        content_mode: "narration",
        segments: [
            {
                segment_id: "E2S01",
                duration_seconds: 4,
                generated_assets: {
                    storyboard_image: "",
                    video_clip: "videos/scene_E2S01.mp4",
                },
            },
        ],
    },
};

test("workspace review helpers should support narration segments and storyboard fallback", () => {
    const selectedReview = { scriptFile: "episode_2.json", itemId: "E2S01" };
    const target = buildReviewTargetFromSelection(narrationScripts, selectedReview, {
        "episode_2.json::E2S01": "storyboards/scene_E2S01.png",
    });

    assert.equal(target.isNarration, true);
    assert.equal(target.storyboardPath, "storyboards/scene_E2S01.png");
    assert.equal(target.videoPath, "videos/scene_E2S01.mp4");
});

test("workspace review helpers should normalize invalid media version values", () => {
    const selectedReview = { scriptFile: "episode_2.json", itemId: "E2S01" };
    assert.equal(
        getReviewMediaVersionForSelection(
            { "episode_2.json::E2S01": "not-a-number" },
            selectedReview
        ),
        0
    );

    const bumped = bumpReviewMediaVersionForItem(undefined, "episode_2.json", "E2S01");
    assert.equal(bumped["episode_2.json::E2S01"], 1);
});

test("workspace review helpers should return explicit not-found error", () => {
    const result = getReviewSelectionResult({}, { scriptFile: "missing.json", itemId: "S1" }, {});
    assert.equal(result.ok, false);
    assert.equal(result.error, "找不到对应片段/场景");
});
