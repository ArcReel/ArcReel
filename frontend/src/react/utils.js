export function cn(...classes) {
    return classes.filter(Boolean).join(" ");
}

export function progressPercent(completed, total) {
    if (!total || total <= 0) return 0;
    return Math.max(0, Math.min(100, Math.round((completed / total) * 100)));
}

function parseDateTime(value) {
    if (!value) return null;
    let normalized = String(value);
    if (normalized.includes(" ") && !normalized.includes("T")) {
        normalized = normalized.replace(" ", "T");
    }
    normalized = normalized.replace(/(\.\d{3})\d+/, "$1");
    const date = new Date(normalized);
    return Number.isNaN(date.getTime()) ? null : date;
}

export function formatDateTime(value) {
    const date = parseDateTime(value);
    if (!date) return "-";
    return date.toLocaleString("zh-CN", {
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
    });
}

export function formatDuration(ms) {
    if (!ms) return "-";
    if (ms < 1000) return `${ms}ms`;
    return `${(ms / 1000).toFixed(1)}s`;
}

export function formatMoney(value) {
    return `$${Number(value || 0).toFixed(2)}`;
}

export function parseStreamData(event) {
    if (!event || typeof event.data !== "string" || !event.data) {
        return {};
    }

    try {
        return JSON.parse(event.data);
    } catch (_error) {
        return {};
    }
}

export function getRoleLabel(role) {
    if (role === "assistant") return "助手";
    if (role === "user") return "你";
    if (role === "tool") return "工具";
    if (role === "tool_result") return "工具结果";
    if (role === "skill_content") return "Skill";
    if (role === "result") return "完成";
    if (role === "system") return "系统";
    if (role === "stream_event") return "流式更新";
    if (role === "unknown") return "消息";
    return role || "消息";
}

export function mapWithUniqueKeys(items, getBaseKey, fallbackPrefix = "item") {
    const list = Array.isArray(items) ? items : [];
    const seen = new Map();

    return list.map((item, index) => {
        const rawBase = getBaseKey ? getBaseKey(item, index) : "";
        const normalizedRaw = rawBase === null || rawBase === undefined
            ? ""
            : String(rawBase).trim();
        const base = normalizedRaw || `${fallbackPrefix}-${index}`;
        const count = seen.get(base) || 0;
        seen.set(base, count + 1);
        const key = count === 0 ? base : `${base}-${count}`;

        return {
            item,
            index,
            key,
        };
    });
}
