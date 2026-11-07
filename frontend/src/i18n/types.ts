// Translation-table guards mirror en structures, widening values to string (translations never constrain literal types).
// zh / vi namespaces lock keys with `satisfies DeepStringify<typeof enX>`,
// rejecting extra/missing keys at typecheck, including nesting.
export type DeepStringify<T> = {
  [K in keyof T]: T[K] extends string
    ? string
    : T[K] extends object
      ? DeepStringify<T[K]>
      : T[K];
};
