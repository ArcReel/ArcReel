import test from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { renderToStaticMarkup } from "react-dom/server";

import {
    LandingPage,
    getNextContactCardOpenOnClick,
} from "../src/react/components/landing-page.js";

function renderLanding(extraProps = {}) {
    return renderToStaticMarkup(
        React.createElement(LandingPage, {
            onEnter: () => {},
            onAssistant: () => {},
            onUsage: () => {},
            ...extraProps,
        })
    );
}

test("landing top bar should use minimal actions", () => {
    const html = renderLanding();

    assert.ok(html.includes("进入管理台"));
    assert.ok(html.includes("联系我们"));
    assert.ok(!html.includes("平台价值"));
    assert.ok(!html.includes("案例"));
});

test("landing hero should only keep one primary cta", () => {
    const html = renderLanding();

    assert.ok(html.includes("进入管理台"));
    assert.ok(!html.includes("立即体验后台"));
    assert.ok(!html.includes("对话管理"));
    assert.ok(!html.includes("费用统计"));
});

test("landing should include discover section with static cards", () => {
    const html = renderLanding();

    assert.ok(html.includes("发现更多"));
    assert.ok(html.includes("剧情故事短片"));
    assert.ok(html.includes("古风悬疑"));
});

test("landing should use compact contact dropdown instead of centered modal", () => {
    const html = renderLanding();

    assert.ok(html.includes("扫码添加微信"));
    assert.ok(!html.includes("点击空白区域关闭"));
});

test("landing grid should not block pointer events", () => {
    const html = renderLanding();

    assert.ok(html.includes("landing-grid pointer-events-none"));
});

test("contact button click toggle should work regardless of hover capability", () => {
    assert.equal(getNextContactCardOpenOnClick(false), true);
    assert.equal(getNextContactCardOpenOnClick(true), false);
});
