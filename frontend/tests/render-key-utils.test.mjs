import test from "node:test";
import assert from "node:assert/strict";

import { mapWithUniqueKeys } from "../src/react/utils.js";

test("mapWithUniqueKeys should deduplicate repeated base keys", () => {
    const items = [
        { id: "AskUserQuestion-1770723118597829356-799" },
        { id: "AskUserQuestion-1770723118597829356-799" },
    ];
    const mapped = mapWithUniqueKeys(items, (item) => item.id, "block");
    assert.deepEqual(
        mapped.map((entry) => entry.key),
        [
            "AskUserQuestion-1770723118597829356-799",
            "AskUserQuestion-1770723118597829356-799-1",
        ]
    );
});

test("mapWithUniqueKeys should fallback when base key is empty", () => {
    const items = [{}, { id: "" }];
    const mapped = mapWithUniqueKeys(items, (item) => item.id, "turn");
    assert.deepEqual(
        mapped.map((entry) => entry.key),
        ["turn-0", "turn-1"]
    );
});
