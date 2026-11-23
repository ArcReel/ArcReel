// 截图基线在默认预览端口上生成，页面上显示本机地址的区域只在这个端口下与基线一致。

export const DEFAULT_E2E_PORT = 4173;

export function isDefaultOrigin(url: string) {
  return new URL(url).port === String(DEFAULT_E2E_PORT);
}
