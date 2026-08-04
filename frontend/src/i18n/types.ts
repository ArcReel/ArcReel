// 翻译表守卫类型：与 en 侧同构，但值放宽为 string（译文不参与字面量类型约束）。
// zh / vi 各命名空间以 `satisfies DeepStringify<typeof enX>` 锁 key 集合，
// 多 key / 少 key 在 typecheck 报错，含嵌套层。
export type DeepStringify<T> = {
  [K in keyof T]: T[K] extends string
    ? string
    : T[K] extends object
      ? DeepStringify<T[K]>
      : T[K];
};
