#!/usr/bin/env bash
# update-docs 引擎 A 的确定性部分：算 baseline、列出全量候选 commit 标题、列出待核对文档。
# 输出供 SKILL.md 的 LLM 步骤消费（agent-facing，无需 i18n）。
set -eu

# 引擎 A 覆盖文档：高频、主题宽，参与 baseline 计算。
ENGINE_A_DOCS=(
  "README.md"
  "docs/getting-started.md"
)

# README 翻译对：英文版是中文版的镜像，不独立进引擎，改完后由主 agent 全文核对一致性。
README_SOURCE="README.md"
README_MIRROR="README.en.md"

# 仅引擎 B 覆盖文档：低频、主题窄，不参与 baseline。
ENGINE_B_ONLY_DOCS=(
  "docs/deployment.md"
  "docs/known-issues.md"
  "docs/jianying-export-guide.md"
  "CONTRIBUTING.md"
)

cd "$(git rev-parse --show-toplevel)"

# baseline：引擎 A 文档中最近一次提交时间的最早者。
# 用 git 提交时间而非文件系统 mtime，后者在 fresh clone 后会失真。
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
  # 一次取全该文档最近一次提交的时间戳、短日期、完整 sha，避免对同一文档多次 git log。
  read -r ts cs sha < <(git log -1 --format='%ct %cs %H' -- "${doc}" 2>/dev/null) || true
  if [ -z "${ts}" ]; then
    echo "- (无 git 历史) ${doc}"
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
