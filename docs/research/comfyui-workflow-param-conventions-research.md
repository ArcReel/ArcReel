# ComfyUI 生态的 workflow 参数化约定

> 状态：调研完成。结论与建议为选型输入，非实现方案。
> 关联：[#2519](https://github.com/ArcReel/ArcReel/issues/2519)（本票）、[#2516](https://github.com/ArcReel/ArcReel/issues/2516)（地图）、[#2522](https://github.com/ArcReel/ArcReel/issues/2522)（被本票阻塞）
> 证据口径：每条结论标注一手来源（GitHub 文件路径 + 符号名，或官方文档 URL）。源码引用基于 2026-09-17 各仓库默认分支。

## 背景与问题

ArcReel 计划让用户导入自己的 API 格式 workflow，导入时自动推断「提示词 / 首帧 / 尾帧 / 参考图 / 宽高 / 时长 / 种子 / 产物」等槽位绑定到哪个节点的哪个字段，并允许用户改。

本调研回答三个问题：

1. 生态里已有哪些「把 workflow 参数化 / 暴露为 API」的约定，各自用什么载体标记输入与输出。
2. 哪些约定「零安装」可用（只改标题、只靠字段名），哪些必须让用户装自定义节点。
3. ArcReel 是否值得兼容其中某家，代价与收益如何。

## 先决事实：API 格式导出物里有什么

ArcReel 的推断只能依赖 API 格式 JSON 里实际存在的字段。官方前端的 `graphToPrompt` 决定了这个集合。

`Comfy-Org/ComfyUI_frontend` 的 `src/utils/executionUtil.ts` 中 `graphToPrompt` 为每个节点产出：

```ts
output[node.id] = {
  inputs,
  // TODO(huchenlei): Filter out all nodes that cannot be mapped to a
  // comfyClass.
  class_type: node.comfyClass!,
  // Ignored by the backend.
  _meta: {
    title: node.title
  }
}
```

由此确认四件事：

- **`_meta.title` 无条件写入每个节点**，值就是画布上该节点的显示标题（用户可自由改名）。这是唯一一个「用户可编辑、且随导出物一起走」的自由文本字段。
- **`_meta` 被后端忽略**（源码注释原文 `Ignored by the backend.`）。这正是它可以被生态各家当作「带外标记通道」的原因：写什么都不影响执行。
- **节点 ID 是导出时的图内 ID**，不是稳定标识。同一张图改动后重新导出，ID 可能变。
- **subgraph 在导出时被展平**。`graphToPrompt` 遍历 `dto.getInnerNodes()`，`ExecutableNodeDTO` 的 `_id` 由 `[...this.subgraphNodePath, this.node.id].join(':')` 生成（`src/lib/litegraph/src/subgraph/ExecutableNodeDTO.ts`）。因此官方虽然有 `SubgraphInput` / `SubgraphOutput` 这套图内输入声明机制（`src/lib/litegraph/src/subgraph/SubgraphInput.ts` 等），**这些声明不会出现在 API 格式导出物里**，ArcReel 无法依赖它们。

官方是否另有参数化约定：**未找到一手来源**。`docs.comfy.org` 的文档索引（`https://docs.comfy.org/llms.txt`）没有「workflow 输入参数」「partial execution」的独立条目；Export (API) 的导出物字段仅 `inputs` / `class_type` / `_meta.title`。Comfy Cloud 在官方文档中亦未见「把 workflow 作为带参 API 调用」的参数标记约定（仅有订阅说明页）。**结论：官方没有定义任何输入输出标记约定，生态各家的约定都是各自发明的。**

## 对比表

| 项目 | 标记载体 | 输入如何标 | 输出如何标 | 多候选消歧 | 需装自定义节点 |
|---|---|---|---|---|---|
| ComfyUI-Deploy（BennyKok） | 专用节点类 | `class_type` 属于 `ComfyUIDeployExternal*` 白名单，参数名取该节点的 `inputs.input_id` | 专用输出节点 `ComfyDeployOutput*`，产物名取 `inputs.output_id` | 无消歧：schema 侧后者覆盖前者，运行侧则**广播**给全部同名节点 | 是 |
| comfy-pack（bentoml） | 专用节点类 + 节点标题 | `class_type` 以 `CPackInput` 开头，**参数名取 `_meta.title`** | `class_type` 以 `CPackOutput` 开头，名同样取 `_meta.title` | 有：重名自动追加节点 ID，`name_{id}` | 是 |
| ComfyUI-Serving-Toolkit | 专用节点类 + input 名 | `Serving Input*` 节点的 `argument` 字段值即参数名，调用方按 `--argument value` 传 | 专用 `ServingOutput` / `ServingTextOutput` / `ServingMultiImageOutput` 节点 | 无：`argument` 重名以 `serving_config` 字典后写覆盖 | 是 |
| ViewComfy | **节点标题前缀 + class_type 白名单 + input 名启发式 + 外置 JSON** | 全量铺开所有标量字段为候选，用户在编辑器里挑，选择结果存进外置 `view_comfy.json`；标题以 `VC_BASIC` / `VC_ADV` 开头即被选为表单项；`class_type` 白名单（`CLIPTextEncode` / `LoadImage` / `VHS_LoadVideo` …）只决定归入基础区还是高级区、以及渲染成哪种控件 | 不标记，按 `class_type` 硬编码（`SaveImage` / `VHS_VideoCombine` 改写 `filename_prefix`） | 靠 key 天然唯一：`<nodeId>-inputs-<field>` | **否**（纯前缀 + 白名单路径零安装） |
| RunComfy（serverless） | 无标记，外置寻址 | 调用时传 `overrides`，按 `<node_id>.inputs.<field>` 直接覆盖 | 不标记，平台收集产物 | 不需要：node_id 天然唯一 | 否 |
| RunningHub | 无标记，外置寻址 | 调用时传 `nodeInfoList`，每项 `nodeId` + `fieldName` + `fieldValue` | 平台侧产物接口 | 不需要：nodeId 天然唯一 | 否 |
| ComfyUI 官方 / Comfy Cloud | 无 | 未定义 | 未定义 | 不适用 | 不适用 |

## 各家的具体标记格式

### ComfyUI-Deploy（BennyKok/comfyui-deploy）

**节点族全清单**（源：`comfy-nodes/*.py` 各文件的 `NODE_CLASS_MAPPINGS`）。输入侧：

`ComfyUIDeployExternalText`、`ComfyUIDeployExternalTextAny`、`ComfyUIDeployExternalImage`、`ComfyUIDeployExternalImageAlpha`、`ComfyUIDeployExternalImageBatch`、`ComfyUIDeployExternalNumber`、`ComfyUIDeployExternalNumberInt`、`ComfyUIDeployExternalNumberSlider`、`ComfyUIDeployExternalNumberSliderInt`、`ComfyUIDeployExternalSeed`、`ComfyUIDeployExternalBoolean`、`ComfyUIDeployExternalEnum`、`ComfyUIDeployExternalLora`、`ComfyUIDeployExternalCheckpoint`、`ComfyUIDeployExternalFaceModel`、`ComfyUIDeployExternalAudio`、`ComfyUIDeployExternalEXR`、`ComfyUIDeployExternalVideo`、`ComfyUIDeployExternalVid`、`ComfyUIDeployExternalFile`、`ComfyDeployWebscoketImageInput`。

节点注册还有一层**自动发现**（`__init__.py`）：扫描 `comfy-nodes/` 后，除并入各模块自带的 `NODE_CLASS_MAPPINGS` 外，任何同时具备 `INPUT_TYPES` 和 `RETURN_TYPES` 的类都会**以 Python 类名**补登记。`ComfyUIDeployExternalEnum` 就是靠这条路进去的（其文件没写 mappings）。这意味着 `class_type` 前缀匹配比枚举白名单更可靠——上游的清单本身就不完整。

输出侧：`ComfyDeployOutputImage`、`ComfyDeployOutputText`、`ComfyDeployOutputEXR`、`ComfyDeployWebscoketImageOutput`（原文拼写如此，`Webscoket` 是上游的拼写错误）。

**输入节点的字段结构**，以 `comfy-nodes/external_text.py` 的 `ComfyUIDeployExternalText.INPUT_TYPES` 为例（原文）：

```python
return {
    "required": {
        "input_id": ("STRING", {"multiline": False, "default": "input_text"}),
    },
    "optional": {
        "default_value": ("STRING", {"multiline": True, "default": ""}),
        "display_name": ("STRING", {"multiline": False, "default": ""}),
        "description": ("STRING", {"multiline": True, "default": ""}),
    }
}
```

`input_id` 是参数名，`display_name` / `description` 是给表单 UI 用的展示元数据。对应的 API 格式片段（原文，来自 `comfy-deploy/comfyui-api-comfydeploy` 的 `workflow_api.json`）：

```json
"17": {
  "_meta": { "title": "External Text (ComfyUI Deploy)" },
  "inputs": {
    "input_id": "filename_prefix",
    "description": "",
    "display_name": "",
    "default_value": "MyVideo"
  },
  "class_type": "ComfyUIDeployExternalText"
},
"16": {
  "_meta": { "title": "External Image (ComfyUI Deploy)" },
  "inputs": {
    "input_id": "input_image",
    "description": "",
    "display_name": "",
    "default_value_url": "https://comfy-deploy-output.s3.us-east-2.amazonaws.com/assets/img_TnmbjHniCjjETWkh.png"
  },
  "class_type": "ComfyUIDeployExternalImage"
}
```

注意 `_meta.title` 在这套约定里**不承载任何语义**，参数名完全来自 `inputs.input_id`。

**schema 生成逻辑**（原文，`web/src/lib/getInputsFromWorkflow.tsx`）：

```tsx
return Object.entries(workflow_version.workflow_api)
  .map(([_, value]) => {
    if (!value.class_type) return undefined;
    const nodeType = customInputNodes[value.class_type];
    if (nodeType) {
      const input_id = value.inputs.input_id as string;
      const default_value = value.inputs.default_value as string;
      return { class_type: value.class_type, input_id, default_value };
    }
    return undefined;
  })
  .filter((item) => item !== undefined);
```

白名单在 `web/src/components/customInputNodes.tsx`（原文），值是给调用方看的类型说明：

```tsx
export const customInputNodes: Record<string, string> = {
  ComfyUIDeployExternalText: "string",
  ComfyUIDeployExternalImage: "string - (public image url)",
  ComfyUIDeployExternalImageAlpha: "string - (public image url)",
  ComfyUIDeployExternalNumber: "float",
  ComfyUIDeployExternalNumberInt: "integer",
  ComfyUIDeployExternalLora: "string - (public lora download url)",
  ComfyUIDeployExternalCheckpoint: "string - (public checkpoints download url)",
  ComfyUIDeployExternalFaceModel: "string - (public face model download url)",
};
```

两个值得记录的细节：

- **白名单比实际节点族窄**。`getInputsFromWorkflow` 只认上面 8 个；`ExternalSeed`、`ExternalEnum`、`ExternalBoolean`、`ExternalVideo`、`ExternalAudio` 等虽然节点存在，却不出现在这份 web 侧 schema 里。生态里「节点先行、schema 后补」的漂移是常态。
- **无消歧，且 schema 侧与运行侧语义不一致**。上面的 `.map()` 产出的是数组，转成键值对后同名的后者覆盖前者。但运行期的 `apply_inputs_to_workflow`（`custom_routes.py`）是**遍历全部节点**逐个匹配的（原文 `for key, value in workflow_api.items()` → `if "input_id" in value["inputs"] and value["inputs"]["input_id"] in inputs`），所以同名节点会**全部**被赋上同一个值。也就是说重名在这套约定里是「一个参数广播驱动多个节点」的隐式语义，既可当 feature 用，也会在用户无意重名时造成不可见的连带改写，两种情况都没有任何警告。

输入取值方式也有值得警惕的一点：`external_image.py` 的 `run()` 把 `input_id` 本身当 URL 试着下载（`urls_to_try = [url for url in [input_id, default_value_url] if url]`），即运行期该字段被平台替换成实际值。这说明**这套约定的「参数名」和「参数值」共用同一个字段**，平台在提交前就地改写 JSON。

输出侧同构，`comfy-nodes/output_image.py` 的 `ComfyDeployOutputImage.INPUT_TYPES` 的 `optional` 里有：

```python
"output_id": ("STRING", {"multiline": False, "default": "output_images"}),
```

**必须装自定义节点**：整套约定的载体是 `class_type`，没有节点就没有 `class_type`，无零安装路径。

### comfy-pack（bentoml/comfy-pack）

这是本次调研中**最值得 ArcReel 借鉴的一家**，因为它把参数名放在 `_meta.title` 上。

**节点族全清单**（原文，`nodes/nodes.py` 的 `NODE_CLASS_MAPPINGS`）：

```python
NODE_CLASS_MAPPINGS = {
    "CPackOutputFile": OutputFile,
    "CPackOutputImage": OutputImage,
    "CPackOutputAudio": OutputAudio,
    "CPackOutputVideo": OutputVideo,
    "CPackOutputZip": OutputImageWithStringTxt,
    "CPackOutputZipSwitch": OutputZip,
    "CPackInputImage": ImageInput,
    "CPackInputString": StringInput,
    "CPackInputInt": IntInput,
    "CPackInputFile": FileInput,
    "CPackInputAny": AnyInput,
    "CPackOutputTextFile": OutputTextFile,
}
```

**关键：输入节点自身没有「参数名」字段。** `StringInput.INPUT_TYPES` 只有 `{"required": {"value": ("STRING", {"default": ""})}}`，`IntInput` 只有 `value` / `min` / `max`，`ImageInput` 只有 `image`。参数名从别处来。

README 把规则说得很直白（原文）：

> The name of a comfy-pack node is the parameter name used for API calls.

实现在 `src/comfy_pack/utils.py` 的 `_get_node_identifier`（原文）：

```python
def _get_node_identifier(node, dep_map=None) -> str:
    """
    Get the input name from the node
    """
    if "_meta" in node and "title" in node["_meta"]:
        title = node["_meta"]["title"]
    else:
        title = ""
    if title.isidentifier():
        return title

    nid = node["id"]
    if dep_map and (nid, 0) in dep_map:
        _, input_name = dep_map[(nid, 0)]
        return _normalize_to_identifier(input_name)

    if not title:
        klass = node.get("class_type", "cpack_input")
        name = klass.lstrip("CPack").lstrip("Input")
        return _normalize_to_identifier(name)

    return _normalize_to_identifier(title)
```

这是一条**四级回退链**，直接对应 ArcReel 需要的「多信号优先级」设计：

1. `_meta.title` 本身是合法 Python 标识符 → 原样用作参数名。
2. 否则看 `dep_map`：该节点的输出连到了下游节点的哪个输入槽，**用下游那个 input 名**作参数名。这是「靠字段名推断」的一手实现范例。
3. 标题为空 → 退回 `class_type` 去掉 `CPack` / `Input` 前缀。
4. 标题非空但不是标识符 → 规范化（`_normalize_to_identifier`：非字母数字下划线全替换为 `_`，数字开头补前缀，折叠连续下划线，去首尾下划线，转小写）。

**扫描与消歧**（原文，同文件 `_parse_workflow`）：

```python
for id, node in workflow.items():
    node["id"] = id
    if node["class_type"].startswith("CPackInput"):
        if not node.get("inputs"):
            continue
        name = _get_node_identifier(node, dep_map)
        if name in inputs:
            name = f"{name}_{id}"
        inputs[name] = node
    elif node["class_type"].startswith("CPackOutput"):
        if not node.get("inputs"):
            continue
        name = _get_node_identifier(node)
        if name in inputs:
            name = f"{name}_{id}"
        outputs[name] = node
```

消歧策略是**重名追加节点 ID**（`f"{name}_{id}"`）。这是三家里唯一显式处理重名的。注意输出分支里的 `if name in inputs` 疑似是上游笔误（应为 `in outputs`），但策略意图清楚。

`dep_map` 的构造也值得抄（原文）：

```python
for id, node in workflow.items():
    for input_name, v in node["inputs"].items():
        if isinstance(v, list) and len(v) == 2:  # is a link
            dep_map[tuple(v)] = node, input_name
```

即：API 格式里 `inputs` 的值若是长度 2 的数组就是连线 `[origin_node_id, origin_slot]`，反查即得「这个节点的输出喂给了谁的哪个字段」。ArcReel 做「顺着连线找语义」时就是这套索引。

**类型与约束推断**（`generate_input_model`，同文件）：`CPackInputString` 取 `str`，`CPackInputInt` 取 `value/min/max` 生成 `Field(default=..., ge=min, le=max)`，`CPackInputImage` / `CPackInputFile` 取 `Path`。`CPackInputAny` 则从 `_meta.options` 读元数据：

```python
elif class_type == "CPackInputAny":
    options = node.get("_meta", {}).get("options")
    value = _get_node_value(node)
    if not options:
        field = (type(value), Field(default=value))
    else:
        if values := options.get("values"):  # combo type
            field = (Literal[tuple(values)], Field(default=value))
        elif any(f in options for f in ("min", "max", "round", "precision", "step")):
            ...
```

这是生态里**唯一一个把结构化元数据塞进 `_meta` 自定义键**的实例（`_meta.options`），利用的正是「后端忽略 `_meta`」这一点。

但要注意 `_meta.options` **不是官方导出物的一部分**，是 comfy-pack 自带的前端扩展劫持 `graphToPrompt` 注入的（原文，`web/dynamic.js`）：

```js
app.graphToPrompt = async function(graph = app.graph, clean = true) {
  const { workflow, output } = await originalToPrompt(graph, clean);
  Object.entries(output).forEach(([id, nodeData]) => {
    if (!nodeData.class_type.startsWith("CPackInput")) return;
    const node = graph.getNodeById(parseInt(id));
    if (!nodeData["_meta"]) {
      nodeData["_meta"] = { title: node.title };
    }
    if (node.widgets.length === 0) return;
    const widget = node.widgets[0];
    nodeData["_meta"] = { ...nodeData["_meta"], options: widget.options };
  });
  return { workflow, output };
};
```

对 ArcReel 的含义：**不能指望用户导入的 workflow 带 `_meta.options`**，除非他们装了 comfy-pack 并用它导出。`_meta.title` 才是官方导出物里唯一稳定可依赖的自由字段。

取值 / 写值一律取节点 `inputs` 的第一个键（`_get_node_value` / `_set_node_value` 用 `next(iter(node["inputs"].values()))`），所以输入节点被设计成只有一个有效字段。

**必须装自定义节点**：`class_type` 前缀是准入条件。`_meta.title` 只在已经确认是 `CPackInput*` 之后才用来取名，不是独立的发现信号。

**`.cpack.zip`**：README 说明它封装 Python 包版本、ComfyUI 与自定义节点 revision、模型哈希，用于重建环境；它不是参数清单的载体——参数仍从 workflow JSON 里的节点推导。

### ComfyUI-Serving-Toolkit（matan1905）

**节点族**（原文，`nodes/all_nodes.py` 的 `NODE_CLASS_MAPPINGS`）：

```python
NODE_CLASS_MAPPINGS = {
    "ServingOutput": ServingOutput,
    "ServingInputText": ServingInputText,
    "ServingInputTextImage": ServingInputTextImage,
    "ServingInputNumber": ServingInputNumber,
    "DiscordServing": DiscordServing,
    "WebSocketServing": WebSocketServing,
    "ServingInputImage": ServingInputImage,
    "ServingTextOutput": ServingTextOutput,
    "ServingMultiImageOutput": ServingMultiImageOutput,
    "ServingInputImageAsLatent": ServingInputImageAsLatent,
    "CommandPickerServing": CommandPickerServing,
    "AlwaysExecute": AlwaysExecute
}
```

**参数标记用 input 名 `argument`**（原文，`ServingInputText`）：

```python
@classmethod
def INPUT_TYPES(s):
    return {
        "required": {
            "serving_config": ("SERVING_CONFIG",),
            "argument": ("STRING", {"multiline": False, "default": "prompt"}),
            "default": ("STRING", {"multiline": True, "default": ""}),
        }
    }

def out(self, serving_config, argument, default):
    if argument not in serving_config:
        return (default,)
    return (serving_config[argument],)
```

结构上与 ComfyUI-Deploy 同类（专用节点 + 节点内一个字段当参数名），差别在于取值是**运行期**从 `serving_config` 字典查，而非提交前改写 JSON。`serving_config` 由 `DiscordServing` / `WebSocketServing` 这类「服务入口节点」注入，调用方在 Discord 消息里用 `--argname value` 形式传参。

消歧：`serving_config` 是普通字典，两个节点写同一个 `argument` 会读到同一个值——严格说这不算冲突，而是「同名即同参」的隐式广播语义，但也意味着无法区分两个本该独立的同名槽位。

参数从外部消息到 `serving_config` 的映射在 `nodes/utils.py` 的 `parse_command_string`（原文）：

```python
def parse_command_string(command_string, command_name):
    textAndArgs = command_string[1 + len(command_name):].strip().split('--')
    result = {}
    result["prompt"] = textAndArgs[0].strip()
    for arg in textAndArgs[1:]:
        parts = arg.split()
        if len(parts) > 1:
            result[parts[0].strip()] = ' '.join(parts[1:]).strip()
    return result
```

即 `!generate 4k portrait --negative drawing` 解析为 `{"prompt": "4k portrait", "negative": "drawing"}`。**自由文本固定落在 `prompt` 这个名字上**，这是生态里唯一一处把「提示词」硬编码为默认槽位的实现。切分极朴素：值不能含 `--`、全部按字符串处理、无引号转义。走 HTTP 入口时则直接拿 POST body 的 JSON 当 `serving_config`，key 即参数名。

输出：`ServingOutput` 的 `out()` 调 `serving_config["serve_image_function"](image, frame_duration)`，即输出通过回调交给服务入口，没有输出命名概念。

**必须装自定义节点。**

### ViewComfy

生态里唯一一家**把「零安装标记」做成一等路径**的开源实现，也是与 ArcReel 需求最贴近的参考。

**三层信号**（源：`lib/workflow-api-parser.ts` 的 `workflowAPItoViewComfy`）。

第一层，**标题前缀**（原文）：

```ts
const VC_BASIC_INPUT = "VC_BASIC";
const VC_ADVANCED_INPUT = "VC_ADV";

function isViewComfyInput(title: string) {
    if (!title) {
        return false;
    }
    return (title.startsWith(VC_BASIC_INPUT) || title.startsWith(VC_ADVANCED_INPUT))
}

function getTitleFromValue(class_type: string, value: { _meta?: { title: string } }): string {
    if (!value._meta?.title) {
        return class_type;
    }
    if (value._meta.title.startsWith(VC_BASIC_INPUT)) {
        return value._meta.title.replace(VC_BASIC_INPUT, "").trim();
    }
    else if (value._meta.title.startsWith(VC_ADVANCED_INPUT)) {
        return value._meta.title.replace(VC_ADVANCED_INPUT, "").trim();
    } else {
        return value._meta.title;
    }
}
```

用户在画布上把节点标题改成 `VC_BASIC 提示词`，该节点就进入表单的基础区，显示名是去掉前缀后的 `提示词`。**不需要装任何节点。**

前缀还兼作**全局开关**：一旦图里出现任何带前缀的节点，解析末尾会丢弃所有未标记的推断结果（原文）：

```ts
if (basicViewComfyInputs.length > 0) {
    basicInputs = [...basicViewComfyInputs];
    advancedInputs = [...advancedViewComfyInputs];
}
```

即「用户一旦开始显式标记，就完全接管自动推断」。这个语义对 ArcReel 的「用户可改」需求很有参考价值。

第二层，**`class_type` 白名单**（无标记时的回退）。`switch (value.class_type)` 分支覆盖 `CLIPTextEncode`（第一个 input 定为 `long-text`）、`LoadImage` / `LoadImageMask` / `loadImage_ViewComfy`（定为 `image`，值清空）、`VHS_LoadVideo` / `LoadVideo`（定为 `video`）、音频加载节点等，命中者进基础区，其余进高级区。

这里要点明一个容易误读的地方：**白名单不是发现过滤器**。`workflowAPItoViewComfy` 先无条件把每个节点的每个标量字段都做成候选（`default` 分支照样把它们推进 `advancedInputs`），白名单只决定「进基础区还是高级区、渲染成哪种控件」。真正的筛选发生在 UI 上——用户在编辑器里挑，挑完的结果存进外置的 `view_comfy.json`（默认文件名见 `app/constants.ts` 的 `viewComfyFileName`）。所以 ViewComfy 的完整形态是「全量候选 + 人工挑选 + 外置落盘」，标题前缀和白名单是用来把好候选顶到前面、减少挑选成本的排序信号。

这个分工对 ArcReel 很有参考价值：**推断的职责不是选对，而是排好序**。ArcReel 既然也有 UI 和「用户可改」的前提，就不必追求单一答案，可以照这个思路把所有合理候选按置信度呈现。

第三层，**input 名启发式**（原文，`default` 分支）：

```ts
for (const input of inputs) {
    if (constants.SEED_LIKE_INPUT_VALUES.some(str => input.title.toLowerCase().includes(str))) {
        input.valueType = "seed";
    }
}
```

配合 `app/constants.ts`（原文）：

```ts
export const SEED_LIKE_INPUT_VALUES = ["seed", "noise_seed", "rand_seed"];
```

这是「只靠字段名」识别种子槽位的完整一手实现。

**寻址 key**（原文，`parseInputField`）：

```ts
const workflowPath = [...path, node.key];
input = {
    title: capitalize(node.key),
    placeholder: capitalize(node.key),
    value: node.value,
    workflowPath,
    valueType,
    validations: { required: isRequired() },
    key: workflowPath.join("-"),
};
```

`path` 传入时为 `[nodeId, "inputs"]`，所以 key 形如 `6-inputs-text`、`31-inputs-seed`。回写时按 `-` 切开逐级下钻（原文，`app/models/comfy-workflow.ts` 的 `setViewComfy`）：

```ts
const path = input.key.split("-");
let obj: any = this.workflow;
for (let i = 0; i < path.length - 1; i++) {
    if (i === path.length - 1) { continue; }
    obj = obj[path[i]];
}
obj[path[path.length - 1]] = input.value;
```

连线值被显式跳过（`if (Array.isArray(node.value)) return undefined;`），即只有字面量字段才是可参数化候选——这条规则 ArcReel 可以直接照搬。

落盘后的 `view_comfy.json` 单项形如（原文，`.cursor/rules/view-comfy-json-rules.mdc`）：

```json
{
  "title": "CLIP Text Encode (Prompt)",
  "placeholder": "CLIP Text Encode (Prompt)",
  "value": "photograph of victorian woman with wings, sky clouds, meadow grass\n",
  "workflowPath": ["6", "inputs", "text"],
  "helpText": "Helper Text",
  "valueType": "long-text",
  "validations": { "required": true },
  "key": "6-inputs-text"
}
```

ViewComfy 的托管 API 用同一套 key，但把整张图摊平成一层字典，并给每个节点插一条 `_` 开头的注释项（原文，`ViewComfy_API/Python/workflow_api_parameter_creator.py`）：

```python
for node_id, node in workflow.items():
    class_type_info = node.get("_meta", {}).get("title") or node.get("class_type")
    flattened[f"_{node_id}-node-class_type-info"] = class_type_info
    if "inputs" in node:
        for input_key, input_value in node["inputs"].items():
            flattened[f"{node_id}-inputs-{input_key}"] = input_value
```

注意这里的 `_meta.title or class_type` 回退——**标题优先、类型兜底**，与 comfy-pack 的取名链同一思路，也正是本文建议 ArcReel 采用的优先级。

**输出不标记**，硬编码在 `class_type` 上（原文，同文件）：

```ts
switch (node.class_type) {
  case "SaveImage":
  case "VHS_VideoCombine":
    node.inputs.filename_prefix = this.getFileNamePrefix();
    break;
```

即改写产物前缀再按前缀回捞，而非声明式指定输出。

**种子随机化**也靠字段名 + 哨兵值：`SEED_LIKE_INPUT_VALUES` 命中且值等于 `Number.MIN_VALUE` 时替换为新随机种子。

**不需要装自定义节点**（白名单里的 `loadImage_ViewComfy` 是可选增强，非必需）。

### RunComfy

**无标记约定**。部署时不声明参数，调用时直接按节点 ID 覆盖字段。请求体（原文，`https://docs.runcomfy.com/serverless/async-queue-endpoints`）：

```json
{
  "overrides": {
    "6": {
      "inputs": {
        "text": "futuristic cityscape"
      }
    },
    "31": {
      "inputs": {
        "seed": 987654321
      }
    }
  }
}
```

文档约束为 node ID 必须存在、input key 必须存在于该节点的 `inputs` 下；未覆盖的字段沿用部署时的默认值。输出由平台收集，不需声明。

需要注意一条二手线索：搜索结果中出现过「参数名须匹配 comfy-pack 节点名」的说法，但**在 RunComfy 官方文档页上未找到一手佐证**，`create-a-deployment` 与 `custom-workflows` 两页均未提及 comfy-pack。按一手来源口径，RunComfy 的公开约定就是上面的 `overrides` 结构。

### RunningHub

**无标记约定**，同样是外置寻址，粒度更细——三元组列表。请求体（原文，`https://www.runninghub.ai/runninghub-api-doc-en/doc-8287469`）：

```json
{
  "webappId": "1937084629516193794",
  "apiKey": "a0fa3e****************345171",
  "nodeInfoList": [
    {
      "nodeId": "39",
      "nodeName": "LoadImage",
      "fieldName": "image",
      "fieldValue": "api/xxxx.jpg",
      "fieldType": "IMAGE",
      "description": "Upload Image"
    },
    {
      "nodeId": "37",
      "nodeName": "RH_ComfyFluxKontext",
      "fieldName": "model",
      "fieldValue": "flux-kontext-pro",
      "fieldType": "LIST",
      "description": "Model Selection"
    }
  ]
}
```

关键在于 **`nodeId` / `fieldName` 不由用户在图上标记，而是先从平台侧接口取「可编辑节点清单」**再据此填值。也就是说消歧和发现工作被平台的解析端承担了，workflow 本身不携带任何标记。`nodeName`（即 `class_type`）、`fieldType`、`description` 是平台返回的辅助元数据，用于生成调用方的表单。

### ComfyUI 官方 / Comfy Cloud

**未找到任何参数化 / 输入标记约定的一手来源。**

- Export (API) 的导出物字段仅 `inputs` / `class_type` / `_meta.title`，见上文 `graphToPrompt`。
- 官方 subgraph 有 `SubgraphInput` / `SubgraphOutput` 输入声明，但在 API 导出时被展平为 `parentId:childId` 形式的扁平节点，声明本身不进导出物。
- `docs.comfy.org` 的 `/prompt` 端点文档只描述「提交 prompt 到队列」，未规定节点字段语义；节点字段的权威描述来自 `/object_info` 端点的运行时响应，而非 workflow 文件里的标记。
- Comfy Cloud 在官方文档中未见带参 API 调用的参数标记约定。

这一点对 ArcReel 是好消息也是坏消息：没有官方标准可遵循（坏），但也没有官方标准会让自定义约定显得离经叛道（好）。

## 零安装可用的约定

按「用户是否必须先装自定义节点」切分，结论很干净：

**零安装可用（只改标题 / 只靠字段名 / 只靠 class_type）：**

- **ViewComfy 的 `VC_BASIC` / `VC_ADV` 标题前缀**——生态里唯一成熟的纯标题约定，用户只需在画布上双击节点改名。
- **ViewComfy 的 `class_type` 白名单**——`CLIPTextEncode` → 提示词、`LoadImage` → 图像、`VHS_LoadVideo` → 视频。零配置，但它在 ViewComfy 里只起排序与控件选型作用，不负责筛除候选。
- **ViewComfy 的「全量候选 + 人工挑选 + 外置落盘」形态本身**——不依赖任何标记，把最终判断交给用户。这是覆盖率最高的零安装方案，代价是首次配置的人工成本。
- **ViewComfy 的 input 名启发式**——`seed` / `noise_seed` / `rand_seed` 识别种子。
- **comfy-pack 的 `_meta.title` 取名规则与 `dep_map` 连线回溯**——取名规则本身零安装（虽然它的准入判据不是），连线回溯「看下游 input 名」更是纯结构推断，不依赖任何标记。
- **RunComfy / RunningHub 的按 ID 寻址**——完全零安装，但也零语义：它们把「哪个字段是提示词」这个问题推给了调用方或平台解析端，没有解决它。

**必须装自定义节点：**

- ComfyUI-Deploy 全部（载体是 `class_type`）。
- comfy-pack 的准入判据（`CPackInput*` / `CPackOutput*` 前缀）。
- ComfyUI-Serving-Toolkit 全部。

需要强调一点：**「零安装」和「无歧义」在生态里是对立的**。装节点的三家之所以要求装节点，正是因为专用 `class_type` 提供了一个不会被误判的准入信号；零安装的 ViewComfy 则必须靠白名单 + 前缀 + 启发式三层信号叠加，并接受一定误判率，再由用户在 UI 上修正。ArcReel 的产品前提（「用户可改」）恰好已经为误判留了出口。

## 对 ArcReel 绑定自动推断的建议

### 是否兼容某家

**建议：不做任何一家的完整兼容，但明确「识别」ComfyUI-Deploy 与 comfy-pack 的节点族，并借鉴 ViewComfy 的三层信号结构与 comfy-pack 的取名回退链。**

理由：

- 完整兼容任何一家都意味着要求用户装那一家的自定义节点，这与「用户导入自己的 workflow」的前提冲突——用户手里的图大概率是原生节点，不带任何标记。
- 但**识别**是廉价的：这三家的标记都在 `class_type` 上，一个前缀匹配即可。若用户的图恰好来自 ComfyUI-Deploy 或 comfy-pack 生态，ArcReel 白捡一份高置信度的绑定，成本是几十行代码。
- 反过来，ArcReel 自己的标记约定应当走 ViewComfy 路线（标题前缀），因为它零安装。

### 标题约定建议格式

借 ViewComfy 的形状，但用更适合 ArcReel 槽位语义的键值形式：

```
ARCREEL:<slot>
```

其中 `<slot>` 取自 ArcReel 的槽位枚举：`prompt` / `negative_prompt` / `first_frame` / `last_frame` / `reference_image` / `width` / `height` / `duration` / `seed` / `output`。用户把节点标题改成 `ARCREEL:first_frame` 即完成绑定。

对应的 API 格式片段（ArcReel 拟定，非现存约定）：

```json
{
  "10": {
    "inputs": { "image": "start.png", "upload": "image" },
    "class_type": "LoadImage",
    "_meta": { "title": "ARCREEL:first_frame" }
  }
}
```

三条设计取舍，都有生态先例支撑：

- **前缀而非整串匹配**，允许 `ARCREEL:first_frame 起始帧` 这样在后面接人类可读说明。取名时截断到第一个空白，这与 ViewComfy 的 `replace(prefix, "").trim()` 同构。
- **标记节点而非标记字段**。生态三家都是「节点级」标记。若一个节点有多个可参数化字段（如某些合一节点同时有 `width` / `height`），退回按 input 名匹配 slot，即 `ARCREEL:size` 之类的节点标记 + 字段名二级判定。
- **一旦出现显式标记，就抑制同槽位的自动推断**。ViewComfy 的 `if (basicViewComfyInputs.length > 0)` 是整图级抑制，ArcReel 建议改成**按槽位抑制**：用户标了 `first_frame` 就只覆盖这一个槽位的推断结果，其余槽位继续自动推断。整图抑制会逼用户一次标全，体验差。

### 三种信号的优先级建议

按置信度从高到低，先命中先定，后续信号只用于填补未定槽位：

| 级 | 信号 | 依据 | 置信度 |
|---|---|---|---|
| 1 | 用户在 ArcReel UI 里的手动绑定 | 产品前提 | 确定 |
| 2 | `_meta.title` 的 `ARCREEL:<slot>` 前缀 | ViewComfy 的 `isViewComfyInput` | 很高 |
| 3 | `class_type` 属于外部约定节点族（`ComfyUIDeployExternal*` / `CPack Input*` / `ServingInput*`），参数名按各家规则取（`inputs.input_id` / `_meta.title` / `inputs.argument`） | 三家源码 | 很高 |
| 4 | `class_type` 属于 ArcReel 的原生节点白名单（`CLIPTextEncode` / `LoadImage` / `LoadImageMask` / `VHS_LoadVideo` / `EmptyLatentImage` / `SaveImage` / `VHS_VideoCombine` …） | ViewComfy 的 `switch (class_type)` | 中 |
| 5 | input 名匹配（`seed` / `noise_seed` / `rand_seed` / `text` / `width` / `height` / `length` / `num_frames` …） | ViewComfy 的 `SEED_LIKE_INPUT_VALUES`，comfy-pack 的 `dep_map` 回溯 | 中偏低 |
| 6 | 连线回溯：顺着某节点输出找到下游消费它的 input 名，用下游语义反推上游槽位 | comfy-pack 的 `_get_node_identifier` 第二级 | 低，但能救「标题空、class_type 泛型」的情况 |

**为什么 `class_type` 排在 input 名之前**：`class_type` 是节点类型的稳定契约，`text` 这个 input 名则可能出现在几十种无关节点上。ViewComfy 也是这个顺序——先 `switch (class_type)`，`default` 分支才做名字匹配。

**为什么标题排在 `class_type` 之前**：标题是用户的显式意图，`class_type` 是推测。用户把第二个 `LoadImage` 标成 `ARCREEL:last_frame`，必须压过「所有 `LoadImage` 都是参考图」的白名单默认。

**多候选消歧**，三条规则，都有先例：

1. **同槽位多候选时不自动选，全部呈现给用户挑**，并按上表的信号级别排序。ArcReel 有 UI，不必像 comfy-pack 那样必须在无人值守下选一个。ViewComfy 已经验证了这条路：它索性不做筛除，只做排序和控件选型，把判断留给用户。
2. **必须选时按节点 ID 升序取第一个**，并在 UI 上标明「自动选择，有 N 个候选」。
3. **参数名重复时追加节点 ID**，照抄 comfy-pack 的 `f"{name}_{id}"`。ComfyUI-Deploy 的「schema 侧覆盖、运行侧广播」是明确的反面教材：同一个重名在两个阶段有两种不同行为，用户无从预期。

另外两条从源码里捡来的实现细节，建议直接采纳：

- **跳过连线值**：`inputs` 里值为长度 2 数组的是连线 `[origin_id, origin_slot]`，不是可参数化字段。ViewComfy 的 `if (Array.isArray(node.value)) return undefined;` 和 comfy-pack 的 `isinstance(v, list) and len(v) == 2` 是同一条规则的两种写法。注意官方前端会把数组类型的 widget 值包成 `{"__value__": [...]}` 以避免与连线混淆（见 `graphToPrompt` 里的 `__type__` / `__value__` 包装），解析时需相应解包。
- **节点 ID 不稳定**：subgraph 展平后 ID 形如 `10:5`；重新导出也可能变。绑定应存 ID + `class_type` + 标题三元组，重新导入时按三元组做模糊重匹配，只存 ID 会在用户更新 workflow 后全部失效。

### 代价与收益

**代价：**

- 第 3 级（识别外部节点族）：小。三家各自的取名规则都是十几行，白名单是常量表。风险是上游节点族会增删——ComfyUI-Deploy 的 web 侧白名单比实际节点族少 10 个，说明连上游自己都跟不上，ArcReel 的白名单必然会漂移，需要当作「尽力而为」而非契约。
- 第 4 级（原生节点白名单）：中。这是长期维护负担，ComfyUI 生态的加载类节点在持续增加（`LoadImage` / `VHS_LoadVideo` / `LoadVideo` / 各类音频加载）。建议把白名单做成数据而非代码，便于随版本补。
- 第 5、6 级（字段名与连线回溯）：小，但误判率最高，必须配合 UI 上的「低置信」提示。
- 自有标题约定：小，但需要文档和引导——用户得知道这个约定存在。ViewComfy 的 `VC_BASIC` 也面临同样的教育成本。

**收益：**

- 零安装是决定性的。用户导入现成的 workflow 就能用，不需要先装 ArcReel 的自定义节点包，这一条直接决定导入功能的转化率。
- 三层信号叠加后，主流文生图 / 图生视频 workflow 的常见槽位（提示词、首帧、宽高、种子、产物）应能在无标记情况下命中大部分。这是 ViewComfy 已经验证过的路径。
- 识别外部节点族让 ArcReel 对「已经在别家平台部署过」的 workflow 有额外优势，这类图的标记质量最高。
- 标题约定给了高级用户一个精确控制的出口，且不污染 workflow 的可执行性（`_meta` 被后端忽略，改标题不影响在原生 ComfyUI 里跑）。

**不建议做的事：**

- 不要求用户装 ArcReel 自有的 External 节点族。走 ComfyUI-Deploy 那条路会把导入门槛抬到与「在 ArcReel 里重建 workflow」相当，失去导入功能的意义。
- 不要照搬 RunComfy / RunningHub 的纯 ID 寻址。那是 API 层的调用协议，不是推断约定，解决不了「哪个字段是提示词」。ArcReel 的绑定结构最终会长得像 RunComfy 的 `overrides`（这是对的，作为回写格式），但推断阶段必须有语义信号。
- 不要依赖官方 subgraph 的输入声明。它在 API 导出时被展平，拿不到。

## 未核实事项

- **RunComfy 是否内部采用 comfy-pack 节点约定**：二手搜索结果有此说法，官方文档页未见佐证，按未确认处理。
- **RunningHub 的「可编辑节点清单」接口如何判定哪些字段可编辑**：文档说明来自平台侧解析，具体判据未公开，无一手来源。
- **Comfy Cloud 的带参调用能力**：官方文档未见相关页面，不能断言「没有」，只能说「未找到一手来源」。
