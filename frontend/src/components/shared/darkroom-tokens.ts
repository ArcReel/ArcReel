/**
 * 由字符串派生一个稳定色相（0-359）。同名同 salt 恒得同色，换名字才换色，
 * 让「没有配图」的资源在整个界面里保持各自固定的身份色。
 */
export function hashHue(name: string, salt: number): number {
  let hash = salt;
  for (let i = 0; i < name.length; i += 1) {
    hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  }
  return hash % 360;
}
