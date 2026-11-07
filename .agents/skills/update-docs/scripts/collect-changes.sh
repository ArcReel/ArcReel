#!/usr/bin/env bash
# Deterministic update-docs gap finder: compute baseline, list all candidate commit titles, and list documents to verify.
# Output feeds the LLM steps in SKILL.md (Agent-facing; no i18n required).
set -eu

# Keep a small enumeration for non-Docusaurus root documents; website/docs coverage tiers derive from page frontmatter.
FULL_ROOT_DOCS=(
  "README.md"
)

# README translation pair: English mirrors Chinese, is not scanned separately, and receives full-pair consistency review by the main Agent after edits.
README_SOURCE="README.md"
README_MIRROR="README.en.md"

# Non-Docusaurus root documents covered only by fact checking.
FACT_CHECK_ONLY_ROOT_DOCS=(
  "CONTRIBUTING.md"
)

cd "$(git rev-parse --show-toplevel)"

FULL_DOCS=("${FULL_ROOT_DOCS[@]}")
FACT_CHECK_ONLY_DOCS=("${FACT_CHECK_ONLY_ROOT_DOCS[@]}")
inventory="$(node website/scripts/update-docs-inventory.mjs --root "$PWD" --format tsv)"
while IFS=$'\t' read -r tier doc; do
  [ -n "${doc}" ] || continue
  case "${tier}" in
    full) FULL_DOCS+=("${doc}") ;;
    fact-check) FACT_CHECK_ONLY_DOCS+=("${doc}") ;;
    none) ;;
    # update-docs-inventory.mjs defines coverage tiers; unknown new values must error rather than silently lose coverage.
    *)
      echo "collect-changes: ${doc} 的覆盖档位「${tier}」本脚本不认识，需同步 case 分支" >&2
      exit 1
      ;;
  esac
done <<< "${inventory}"

# Baseline: the earliest latest-commit timestamp among full-coverage documents.
# Use git commit timestamps, not filesystem mtime, which becomes inaccurate after fresh clones.

# Document freshness: latest commit that changed the body. Skip frontmatter-only commits:
# tier migrations and title / sidebar_position changes are metadata. Counting them as fresh advances
# the baseline to that edit, permanently excluding earlier capability changes from gap scans.
# Compare bodies before/after stripping frontmatter, rather than matching diff lines:
# line regexes cannot enumerate every frontmatter field; missed fields would look like body changes.

# Strip initial frontmatter (first --- through next ---, including delimiters), keeping only body.
strip_frontmatter() {
  awk 'NR == 1 && $0 == "---" { fm = 1; next } fm { if ($0 == "---") fm = 0; next } { print }'
}

# Output <rev>:<path> contents; output empty if that revision lacks the file (including root commits without parents).
# Check exit status explicitly rather than relying on set -e, which is ineffective inside command substitutions;
# git show failures from an incomplete object database would otherwise masquerade as missing bodies.
blob_content() {
  local blob
  if ! blob="$(git rev-parse -q --verify "$1" 2>/dev/null)"; then
    return 0
  fi
  git show "${blob}"
}

content_freshness() {
  local target="$1" history ts cs sha current parent
  if ! history="$(git log --format='%ct %cs %H' -- "${target}")"; then
    echo "collect-changes: 读不到 ${target} 的提交历史，无法定新鲜度点" >&2
    return 1
  fi
  while read -r ts cs sha; do
    [ -n "${sha}" ] || continue
    if ! current="$(blob_content "${sha}:${target}")"; then
      echo "collect-changes: 读不到 ${target} 在 ${sha} 的内容，无法判定是否正文刷新" >&2
      return 1
    fi
    if ! parent="$(blob_content "${sha}^:${target}")"; then
      echo "collect-changes: 读不到 ${target} 在 ${sha} 父提交的内容，无法判定是否正文刷新" >&2
      return 1
    fi
    if [ "$(printf '%s\n' "${current}" | strip_frontmatter)" != "$(printf '%s\n' "${parent}" | strip_frontmatter)" ]; then
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

echo "## 全量组文档（缺漏扫描 + 事实核对，参与 baseline）"
for doc in "${FULL_DOCS[@]}"; do
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
  echo "## 错误：没有任何全量组文档有 git 历史，无法定 baseline"
  exit 1
fi

echo
echo "## baseline（仅基于全量组文档）"
echo "最早被改动的全量组文档：${baseline_doc}（${baseline_cs}）"
echo "扫描区间：${baseline_sha:0:9}..HEAD"

# All candidate commits: every non-merge commit in the interval, SHA + title only.
# Do not filter type/scope: Conventional Commits are convention rather than enforcement here,
# so filtering is unreliable; leave semantic relevance to the gap-scan subagent.
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
[ "${count}" -eq 0 ] && echo "（区间内无候选改动，全量组文档可能已是最新）"

# Full fact-check list: verify every in-scope document.
echo
echo "## 事实核对文档清单（每篇派一个只读 subagent）"
for doc in "${FULL_DOCS[@]}" "${FACT_CHECK_ONLY_DOCS[@]}"; do
  [ -f "${doc}" ] && echo "- ${doc}"
done

echo
echo "## README 翻译对（中文为源）"
echo "${README_MIRROR} 是 ${README_SOURCE} 的镜像，改完后由主 agent 全文核对一致性。"
exit 0
