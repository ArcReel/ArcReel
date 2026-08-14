#!/usr/bin/env bash
# update-docs 引擎 A 的确定性部分：算 baseline、列出全量候选 commit 标题、列出待核对文档。
# 输出供 SKILL.md 的 LLM 步骤消费（agent-facing，无需 i18n）。
set -eu

# 非 Docusaurus 根目录文档保留少量枚举；website/docs 的归属由各页 frontmatter 派生。
ENGINE_A_ROOT_DOCS=(
  "README.md"
)

# README 翻译对：英文版是中文版的镜像，不独立进引擎，改完后由主 agent 全文核对一致性。
README_SOURCE="README.md"
README_MIRROR="README.en.md"

# 非 Docusaurus 根目录中仅引擎 B 覆盖的文档。
ENGINE_B_ONLY_ROOT_DOCS=(
  "CONTRIBUTING.md"
)

cd "$(git rev-parse --show-toplevel)"

ENGINE_A_DOCS=("${ENGINE_A_ROOT_DOCS[@]}")
ENGINE_B_ONLY_DOCS=("${ENGINE_B_ONLY_ROOT_DOCS[@]}")
inventory="$(node website/scripts/update-docs-inventory.mjs --root "$PWD" --format tsv)"
while IFS=$'\t' read -r ownership doc; do
  [ -n "${doc}" ] || continue
  case "${ownership}" in
    engine-a) ENGINE_A_DOCS+=("${doc}") ;;
    engine-b) ENGINE_B_ONLY_DOCS+=("${doc}") ;;
    none) ;;
    # 归属取值的真相源在 update-docs-inventory.mjs；新增取值而漏改这里时报错，不静默漏覆盖。
    *)
      echo "collect-changes: ${doc} 的归属「${ownership}」本脚本不认识，需同步 case 分支" >&2
      exit 1
      ;;
  esac
done <<< "${inventory}"

# baseline：引擎 A 文档中最近一次提交时间的最早者。
# 用 git 提交时间而非文件系统 mtime，后者在 fresh clone 后会失真。

# 文档的新鲜度点：最近一次改动过正文的提交。只改 update_docs 归属声明的提交要跳过——
# 归属迁移与日后的归属重划都是纯元数据编辑，算作新鲜会把 baseline 推到该次编辑，
# 使编辑之前那段区间的能力变更永远不再进入引擎 A 扫描。
content_freshness() {
  local target="$1" history ts cs sha
  # 显式判退出码，不靠 set -e：函数在命令替换里被调用时 set -e 不生效，
  # 对象库不完整导致的 git log 失败会伪装成「该文档没有历史」，把它从 baseline 里悄悄摘掉。
  if ! history="$(git log --format='%ct %cs %H' -- "${target}")"; then
    echo "collect-changes: 读不到 ${target} 的提交历史，无法定新鲜度点" >&2
    return 1
  fi
  while read -r ts cs sha; do
    [ -n "${sha}" ] || continue
    # 归属声明与其所在的 frontmatter 分隔符都不算正文：页面原本没有 frontmatter 时，
    # 补声明的提交新增的是整块 `---` / `update_docs` / `---`。
    if git show --format= -U0 "${sha}" -- "${target}" |
      grep -E '^[+-]' | grep -qvE '^(\+\+\+ |--- |[+-](---$|update_docs:))'; then
      echo "${ts} ${cs} ${sha}"
      return 0
    fi
  done <<< "${history}"
  return 0
}

baseline_ts=""
baseline_sha=""
baseline_cs=""
baseline_doc=""

echo "## 引擎 A 覆盖文档（参与 baseline）"
for doc in "${ENGINE_A_DOCS[@]}"; do
  if [ ! -f "${doc}" ]; then
    echo "- (缺失) ${doc}"
    continue
  fi
  if ! freshness="$(content_freshness "${doc}")"; then
    exit 1
  fi
  read -r ts cs sha <<< "${freshness}" || true
  if [ -z "${ts}" ]; then
    echo "- (无正文改动历史) ${doc}"
    continue
  fi
  echo "- ${doc} 最近改动 ${cs}"
  if [ -z "${baseline_ts}" ] || [ "${ts}" -lt "${baseline_ts}" ]; then
    baseline_ts="${ts}"
    baseline_sha="${sha}"
    baseline_cs="${cs}"
    baseline_doc="${doc}"
  fi
done

if [ -z "${baseline_sha}" ]; then
  echo
  echo "## 错误：没有任何引擎 A 文档有 git 历史，无法定 baseline"
  exit 1
fi

echo
echo "## baseline（仅基于引擎 A 文档）"
echo "最早被改动的引擎 A 文档：${baseline_doc}（${baseline_cs}）"
echo "扫描区间：${baseline_sha:0:9}..HEAD"

# 全量候选 commit：区间内所有非 merge commit，每条仅 sha + 标题。
# 不做 type/scope 过滤——Conventional Commits 在本项目是约定而非强制，
# 基于它的过滤不可靠；相关性判断交由引擎 A subagent 在语义层完成。
echo
echo "## 候选 commit（baseline..HEAD 全量，每条 sha + 标题）"
count=0
while IFS=$'\t' read -r sha subject; do
  [ -n "${sha}" ] || continue
  count=$((count + 1))
  echo "${sha:0:9} ${subject}"
done < <(git log "${baseline_sha}..HEAD" --no-merges --format=$'%H\t%s')

echo
echo "## 候选 commit 总数：${count}"
[ "${count}" -eq 0 ] && echo "（区间内无候选改动，引擎 A 文档可能已是最新）"

# 引擎 B 全量清单：所有 in-scope 文档都要核对。
echo
echo "## 引擎 B 全量核对文档清单（每篇派一个只读 subagent）"
for doc in "${ENGINE_A_DOCS[@]}" "${ENGINE_B_ONLY_DOCS[@]}"; do
  [ -f "${doc}" ] && echo "- ${doc}"
done

echo
echo "## README 翻译对（中文为源）"
echo "${README_MIRROR} 是 ${README_SOURCE} 的镜像，改完后由主 agent 全文核对一致性。"
exit 0
