# 新增与升级依赖

1. **用包管理器写入。** 后端 `uv add <包>`（dev 依赖加 `--group dev`），前端与文档站在各自目录 `pnpm add <包>`。版本号与 lockfile 由包管理器写入，不在 `pyproject.toml` / `package.json` 中手写。
2. **归入 Dependabot 分组。** 在 `.github/dependabot.yml` 对应生态的 `groups` 里把新包加进语义最接近的分组。`all-other` 只收未归类的包，不能当作新包的分组；`uv run python scripts/audit_conventions.py --check` 会列出只命中 `all-other` 的直接依赖。
3. **跑依赖卫生检查。** `uv run deptry lib server alembic scripts tests`；改了子包时另在 `packages/arcreel-market-core/` 跑 `uv run deptry src tests`。直接 import 的第三方包必须声明（`DEP003`），声明了却没有 import 的包要删除或登记为运行时插件（`DEP002`）。

## 已知版本约束

- **前端 TypeScript 受 `typescript-eslint` 的 peer 范围限制。** 升级 TypeScript 前先核对锁定版本的 `typescript-eslint` peer 范围，必要时同步升级。
- **文档站 TypeScript 上限是 6.x。** 原因与解锁条件写在 `.github/dependabot.yml` 的 website 段注释里。
- **`pyproject.toml` 与 `frontend/package.json` 的 `version` 字段由 release-please 维护**，视为只读。
