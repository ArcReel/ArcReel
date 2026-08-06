---
name: generate-assets
description: 资产设计图：生成、重生或局部编辑角色、场景和道具 sheet。
---

# 资产设计图

## 步骤

1. 调 workflow status，取得 character / scene / prop 的 missing、stale、current ID。
2. 用户要求修改图片时读取 `.claude/references/edit-or-regenerate.md`。
3. 语义变更先用 `patch_project` 修改资产 `description`；局部像素修改用 `edit_images`。
4. 生成调用：

```text
mcp__arcreel__generate_assets({})
mcp__arcreel__generate_assets({"type": "character"})
mcp__arcreel__generate_assets({"type": "prop", "names": ["玉佩", "密信"]})
```

省略 names 处理该范围内所有 missing 与 stale；显式 names 强制重生。
5. 按 `.claude/references/completion-contract.md` 验证 requested IDs。

## 描述口径

- 角色：稳定外貌、服装、配饰和色彩；
- 场景：空间、光线、氛围和标志结构，不写人物；
- 道具：形态、材质、尺寸和可识别细节。

布局、反向提示和供应商适配由服务端 prompt builder 负责，skill 不复制这些模板。
