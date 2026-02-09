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
    if (role === "result") return "完成";
    return role || "消息";
}
