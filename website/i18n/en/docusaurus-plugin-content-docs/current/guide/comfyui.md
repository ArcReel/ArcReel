---
id: comfyui
title: Connect a ComfyUI Workflow
sidebar_position: 4
---

# Connect a ComfyUI Workflow {#comfyui}

ArcReel can use a ComfyUI API-format workflow as an image or video call endpoint. The workflow defines its models, nodes, and fixed parameters. Node bindings tell ArcReel where to write request data such as prompts, dimensions, and assets, and which node contains the final artifact.

If you only need to configure a cloud model, start with [Provider and Model Configuration](./providers.md). This page is for setups where you operate the ComfyUI service and workflow.

## 1. Prerequisites and the Connectivity Check {#prerequisites}

Before you begin, prepare:

- A ComfyUI instance reachable over HTTP from the ArcReel server;
- A workflow that runs successfully in ComfyUI, with all required models and custom nodes installed;
- An **API-format** JSON export from ComfyUI;
- The Base URL and API Key if ComfyUI is behind an authenticated reverse proxy.

ArcReel talks to ComfyUI over HTTP and nothing else. Assets are uploaded through `POST /upload/image`, and artifacts are retrieved through `/history` and the download endpoint, so the two machines **do not need a shared filesystem** and you do not need to mount ArcReel's media directory into ComfyUI. Running ComfyUI locally or on another machine takes the same code path and the same configuration — a local install just means a local address in the Base URL.

### 1.1 Create a comfyui Provider {#create-provider}

In Settings → Providers, add a provider, choose the `comfyui` protocol, and enter the Base URL. ComfyUI itself is normally unauthenticated, so the API Key may be left blank; fill it in only when a reverse proxy requires it. This protocol has no model-discovery step: one workflow is one model, and you attach a call endpoint to each model row yourself (see section 2).

On save, the connectivity check sends the API Key as a **bare Bearer token to `/system_stats`** — with no credentials at all when the key is blank — and the reported ComfyUI version is echoed back on the settings page.

If your reverse proxy authenticates with a **custom header** rather than `Authorization: Bearer`, the proxy rejects this probe and it **reports the instance as unreachable**: the probe does not use the custom headers declared in an endpoint definition's `auth` section. Its verdict carries no information in that case, so rely on the endpoint's Request Preview and Connection Test instead — those paths do inject the API Key into headers or query parameters according to the `auth` section.

## 2. Export and Import the Workflow {#import}

ComfyUI's export menu has two entries, and only one of them works here:

- **Export** produces a canvas archive with a `nodes` array and `links`, recording node positions and wiring. It has no `class_type`, and ComfyUI's own `/prompt` endpoint does not accept that shape, **so ArcReel refuses it** and points you at the other menu entry.
- **Export (API)** produces a node table keyed by node id — exactly the shape submitted to `/prompt`. Import this one.

Import steps:

1. In Settings → Call Endpoints, import the API workflow JSON.
2. Give the endpoint a descriptive name and confirm whether its media type is image or video.
3. Review the detected node bindings (see section 3).
4. Confirm the positive prompt and output node. Both require a target, and two semantic bindings cannot occupy the same field.
5. Save the endpoint, then attach it to a model row of your ComfyUI provider.

One model row represents one workflow. Fixed values such as checkpoints, LoRAs, and samplers remain in the workflow rather than the model row. To change them, edit the workflow in ComfyUI, export it again, and update the endpoint with Re-import. Re-import attempts to rematch existing bindings by node identity; manually review every item marked for confirmation.

## 3. Node Bindings {#bindings}

ComfyUI has no shared convention for parameters: each node in an export carries only `inputs`, a `class_type`, and a title, so which node takes the prompt and which one emits the final media can only be inferred. ArcReel confines that inference to the import step and produces **candidates** only: a single highest-scoring candidate is marked as detected, tied candidates are left for you to choose between, and when there is no candidate at all you can pick any node input by hand. Mark capabilities this workflow genuinely lacks as unsupported — ArcReel then stops inferring them and never writes them into the graph.

### 3.1 Common Bindings {#binding-table}

| Binding | ArcReel behavior |
|---|---|
| Positive prompt | Writes the prompt body without `Avoid:` lines; required |
| Negative prompt | Appends exclusions from the prompt's `Avoid:` lines to the node's existing literal value |
| Start frame, end frame, reference images | Uploads assets supplied for this request and writes the references returned by ComfyUI into image-loader nodes |
| Width, height | Converts the aspect ratio and resolution tier, then rounds each value down to its alignment step |
| Frame count | Converts seconds using the frame rate and aligns the result to `step × n + 1` |
| Frame rate | Read-only binding; ArcReel reads the workflow literal for its conversions and never overwrites it |
| Seed | Rewrites the seed according to that entry's policy |
| Output node | Retrieves the final image or video from this node; required |

### 3.2 Pinning a Node {#pinning-nodes}

When inference picks the wrong node, or too many candidates tie, there are two ways to avoid choosing by hand every time:

- **The title convention.** Rename a node in ComfyUI to `ARCREEL:<semantic key>` (for example `ARCREEL:prompt`, `ARCREEL:seed`, `ARCREEL:output`). ArcReel treats this as the strongest signal short of a manual binding, outranking any guess derived from input names or node types. The prefix is case-insensitive, the first word after the colon is the key, and anything after that is yours to use as a note. The marker lives in the workflow, so it survives re-export and re-import.
- **External node families.** If the workflow already declares its external parameters through one of these node families, ArcReel honors that declaration and you need not touch any titles:

  | Node family | Class-type prefix | Parameter name read from |
  |---|---|---|
  | ComfyUI-Deploy | `ComfyUIDeployExternal` | the `input_id` input |
  | comfy-pack | `CPackInput` | the node title |
  | ComfyUI-Serving-Toolkit | `ServingInput` | the `argument` input |

  The declared name is normalized (lowercased, runs of non-alphanumerics collapsed to underscores) and must then equal the semantic key — so `Reference Images` and `reference_images` are the same thing.

Both mechanisms only raise a candidate's score. What finally counts is the binding you confirm and save on the import screen.

## 4. Size, Duration, and Seed {#parameters}

### 4.1 Fixed Size {#fixed-size}

ArcReel rewrites dimensions only when **both** width and height are bound. Binding one side is not enough: the derived values can only be written into the side that is bound, so the output ratio is neither the workflow's own nor the one you picked — worse than binding neither. A missing side therefore makes the whole dimension fixed, the resolution selector is disabled, and the size follows the workflow.

When both sides are bound and every bound input reads back the same positive integer literal, the resolution selector's empty-value placeholder shows the native tier inferred from it (for example "workflow native (480p)"), so you can see what you get by not choosing a tier. It is omitted when the literals cannot be read or disagree. The fixed-size branch has no such value at all — there, the UI states outright that the workflow controls the size.

Image tiers are `512px`, `1K`, `2K`, and `4K`; video tiers are `480p`, `720p`, `1080p`, and `4K`. They are conversion targets, not a guarantee that every workflow or GPU configuration can run them.

### 4.2 Fixed Duration and Empty Tiers {#fixed-duration}

An empty video duration tier list has three distinct causes, and the UI names them separately:

- **`frames` is not bound.** ArcReel has no pointer to the input holding the frame count, so the workflow fixes the duration and no frame count is written.
- **`frames` is bound, but there is no frame-rate source** — neither a read-only `fps` binding nor a frame rate typed into the frames entry — so seconds cannot be converted. This does not mean the workflow has a fixed duration; it is a **fixable definition**. Add an `fps` binding, or type a frame rate on the frames entry: ArcReel needs one of them before it can work out any duration tiers (and even then the definition may land in the next case).
- **`frames` is bound and a frame rate is readable, but no whole-second tier writes back to this workflow unchanged.** For example, 81 frames at 24fps rounds to 3 seconds, but converting 3 seconds back yields 73 frames; a frame-count input fed by a link, or holding a placeholder such as `1`, has no readable clip length and yields no tier either. A tier labeled "native" that alters the graph when selected is worse than no tier at all, so none is offered. Only the first shape — every frame-count input holding its own clip length — also leaves the frame count alone on submission; the other two have no clip length to preserve, so the frame count is still written from the duration the planning layer borrows.

Outside those three, when frame count and frame rate are both usable and write back unchanged, ArcReel derives the workflow's native duration as `seconds = round((literal frames − 1) ÷ fps)` and offers it as the default tier; "writes back unchanged" means the inverse conversion `frames = round(seconds × fps) + 1`, aligned to the step, still equals the literal frame count. You can add or remove tiers on the model row.

An empty tier list is not a generic "no durations configured for this model" error. Script planning still borrows a set of reference tiers for content length so it is not blocked, but the workflow always determines the actual output duration.

### 4.3 Three Seed Policies {#seed-policies}

The policy is recorded per seed entry, and one workflow may mix them:

- **No seed binding:** ArcReel leaves the workflow's seed fields untouched.
- **`random`:** uses a requested seed when present, otherwise generates a new seed for each submission. All `random` entries in one workflow receive the same value — letting two samplers roll independently would make a single generation irreproducible.
- **`keep`:** not a single byte changes; even a requested seed cannot override it.

Video results record the seed that was **actually in effect** (the workflow literal when every entry is `keep`) along with the fingerprint of the submitted workflow, so you can check which graph and which seed produced a given version.

### 4.4 Rewriting the Graph for Missing Assets {#dropping-nodes}

Start frames, end frames, and reference images all trigger a graph rewrite when they are bound but not supplied for this request: an image-loader node left in place would read the filename hard-coded in the workflow, and the model would generate from a picture you never chose.

- **Start and end frames:** the loader node is removed when no value is supplied, together with downstream branches that are no longer reachable.
- **Reference images:** images fill the slots in order and surplus slots are removed — but only when ArcReel recognizes the input a slot feeds as optional or as one side of a two-way merge node. When it cannot tell, nothing is removed and the last image is repeated into the surplus slots instead: a slightly off composition beats guessing a required input away and having ComfyUI reject the submission. If no image is supplied at all, the loader nodes keep their literal values.

If a branch to be removed reaches the output chain, the request fails with `comfyui_image_drop_unsupported`; supply the missing images or use a workflow that accepts that input combination.

## 5. Prompts {#prompt}

Prompts are sent verbatim. ArcReel neither translates nor rewrites them, so **pick a model that reads the language you write in**. A Chinese prompt only works with a model that understands Chinese; otherwise the model is generating from text it cannot read. On the video side, Wan officially recommends Chinese prompts; for images, Qwen-Image and Kolors are bilingual. Most other models are trained predominantly on English, and composition or motion instructions written in Chinese are often ignored — rewriting the prompt in English helps more than retuning parameters.

`Avoid:` lines in the prompt body are never sent as part of the body. When a negative-prompt node is bound, those exclusions are appended to that node's existing literal value; **without that binding they are simply discarded** — absent from the body and absent from the workflow. If your workflow has a dedicated negative-prompt node, bind it.

## 6. Preview and Connection Test {#test}

The endpoint detail page offers two test cards that share the same parameters, credentials, and assets — change any of them in between and the two cards no longer describe the same thing:

- **Request Preview** renders the workflow, authentication, and conversions that would be sent to `/prompt`, without contacting ComfyUI. Credentials are masked, and asset fields show a placeholder description rather than the reference name a real upload would return.
- **Connection Test** is offered for **video endpoints only** (image endpoints have the preview card alone). It really uploads assets, submits the workflow, polls execution, and downloads the artifact, so it **occupies the ComfyUI queue and GPU**. Use the preview first to check nodes, dimensions, frame count, seed, and which nodes this request removes.

Test runs appear in the ComfyUI queue as `endpoint-test-…`, with a random suffix so repeated tests of the same endpoint do not overwrite each other's uploads. When a run is canceled or times out, ArcReel makes a best-effort attempt to stop it remotely: newer ComfyUI versions support cancellation by job ID; on older ones ArcReel inspects the queue, drops the item if it is still pending, and interrupts execution only after confirming this job is the one currently running — `/interrupt` ignores ids, and guessing wrong would stop someone else's work. A network failure can prevent the remote stop, so check the ComfyUI queue after cancelling.

## 7. Operations and Storage {#operations}

- Uploaded assets are stored under ComfyUI's `input/arcreel/`, separate from images you upload yourself. ArcReel **does not clean them up automatically** and they accumulate with use; monitor disk space according to your own retention policy and delete the directory's contents when needed.
- Default concurrency is 1. On a single-GPU ComfyUI instance, raising it usually only creates a longer remote queue; increase it only when the machine has capacity.
- **The video poll timeout is configurable** under Settings → Video poll timeout (seconds), minimum 60, applying to tasks that start processing after the change. ComfyUI runs on your own GPU and queues work, so the default may be too short for large graphs — set it from your real generation times. Image tasks have no such setting and use a fixed, generous limit.
- After submitting a video task, ArcReel stores its ComfyUI `prompt_id`. Following an ArcReel restart, it resumes polling that task without uploading assets or submitting the workflow again.
- Image tasks cannot resume after a restart: there is nowhere to persist the `prompt_id`, so a restart mid-generation marks the task lost rather than making your GPU render the same image twice.
- When a run emits several files, both channels take the first artifact whose extension matches the endpoint's media type; video tasks additionally attach a warning to the result, while image tasks only log it.

## 8. Common Failures {#troubleshooting}

| Failure code | Meaning and next step |
|---|---|
| `comfyui_upload_failed` | The asset upload failed. Check the address, credentials, network, and ComfyUI disk, then retry |
| `comfyui_node_errors` | ComfyUI rejected the workflow, usually because a model is missing, a node does not exist, or a parameter is out of range. Fix it in ComfyUI and re-import |
| `comfyui_job_lost` | The `prompt_id` is absent from both queue and history, usually after a ComfyUI restart. Generate again |
| `comfyui_execution_error` | A node failed during execution. Use the reported node to inspect models, nodes, and inputs in ComfyUI |
| `comfyui_interrupted` | A person or another client interrupted the run. Confirm the queue state and retry |
| `comfyui_output_missing` | The bound output node emitted no file. Check that node and its upstream output chain |
| `comfyui_output_type_mismatch` | The artifact extension does not match the endpoint media type. Rebind the node that exports the final media |
| `comfyui_image_drop_unsupported` | Removing a branch for a missing asset would affect the output. Supply the asset or use another workflow |

`comfyui_upload_failed`, `comfyui_job_lost`, and `comfyui_interrupted` are transient environment problems, and both the failure card and the project page point at retrying. The remaining five point at the endpoint configuration: change a binding in the endpoint detail, or fix the workflow in ComfyUI and re-import it.

If a task reports multiple outputs but keeps only one, that is the current channel selection rule; it does not mean ComfyUI generated fewer files. Confirm the output node in the preview, then inspect the complete output in ComfyUI history.

## 9. Cost and Execution Time {#cost}

ComfyUI runs on your own hardware, and ArcReel has no way to know what a generation was worth, so it **records every call at zero cost** by default; connection tests are recorded the same way. Execution time is recorded as usual, so these calls appear on the usage page with full durations and counts — only the amount column is zero.

To fold the cost of your own GPUs into ArcReel's usage figures, enter a unit price and currency of your own on the provider's model row: images are charged per call, videos per second multiplied by duration. Calls are then recorded with an amount derived from that price. The number is entirely yours to decide — ArcReel never estimates it for you, and setting it changes nothing about generation itself.

A zero cost does not mean free: it costs electricity, GPU occupancy, and queue time. Avoid repeated tests while another important job is using the same GPU.
