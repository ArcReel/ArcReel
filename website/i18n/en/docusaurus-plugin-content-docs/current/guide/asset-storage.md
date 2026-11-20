---
id: asset-storage
title: Asset Libraries and Object Storage
sidebar_position: 5
---

# Asset Libraries and Object Storage {#object-storage-and-asset-library}

Settings → Asset Library & Storage has two independent options. Both are optional. Projects use local files by default.

- To let a model reference images reviewed by the provider, set up an asset library account and bind its inference credential. Then [upload an image](#upload-to-asset-library) or [select an existing asset](#select-from-asset-library).
- To keep newly saved images in your own TOS bucket, [enable object storage](#remote-image-storage).
- To move assets, check how the [global asset library](#asset-library-across-projects) and [project ZIPs](#asset-library-in-project-zip) handle image versions and links.

## Configure Connections and Credentials {#configure-asset-storage}

Volcano Engine Ark and BytePlus can keep reviewed materials in their provider asset libraries for use as `asset://` references during generation.

- **Object storage**: your own TOS bucket, configured with region, Endpoint, bucket name, and Access Key. Saving the connection alone does not change where images are stored, and new images are still saved locally by default. To save new images to this bucket, also choose **Object storage** under **Save new images to**; see [Save New Images to Object Storage](#remote-image-storage). Enter a TOS Endpoint (such as `tos-cn-beijing.volces.com`); S3-compatible Endpoints that start with `tos-s3-` are not supported. After saving, use **Test connection** to confirm that the credentials can reach the bucket.
- **Asset library accounts**: the Access Key for the asset library control plane. It is stored separately from the inference API Key and needs asset library permissions. When adding an account, choose a site (Volcano Engine (China) or BytePlus (international)) and enter a project; the default project is `default`. The provider isolates assets by project, so the inference key must belong to the same project. Control-plane requests go only to the official address of the selected site, never to the inference key's Base URL. A provider asset belongs only to the cloud account that created it, so links and ingestion jobs record the cloud account that the account's Access Key ID belonged to at the time. After you replace the Access Key ID, existing links stay with the original cloud account and are no longer used for generation. Unfinished ingestion jobs do not proceed with the new Access Key. Upload or select the image again under the new account. Changing only the Secret Access Key does not affect existing links.
- **Inference key bindings**: for each Volcengine Ark key or custom provider, choose the asset library account it may use. After you change the inference key's secret or Base URL, or the account's site, project, or Access Key ID, the binding shows **Needs confirmation**, and that account's assets are not used until you confirm again. A binding only records that you confirmed the inference key and the account belong together; the provider still checks permissions on every call.

Access Keys are entered only on the settings page and are shown with only their first and last few characters when read back.

## Upload Images to an Asset Library {#upload-to-asset-library}

Choose **Upload to provider asset library** from the more-actions menu on a project asset card or on a derivative in the character details to upload the currently selected version of the image to each asset library account. Object storage must be configured first: ArcReel writes a copy of the image to your bucket, gives the provider a download URL valid for up to 24 hours, and then submits the asset. ArcReel therefore does not need a public address.

- The provider reviews uploads asynchronously. The status moves through **Waiting to upload**, **Submitting**, and **Provider processing**, and ends as **Available** or **Failed**. Closing the window does not stop the upload, and only **Available** links are used for generation. On failure, the provider's error code and reason are shown.
- A link belongs to a specific version of the image. If you select another version during the upload, the result still belongs to the version that was submitted. The newly selected version must be uploaded separately; switching back to the old version uses its link again.
- If the result of the submission is unknown (for example, the network dropped), the status shows **Result unconfirmed**. ArcReel looks up the uploaded asset in the asset library from the submission record and does not create it again. If nothing turns up for a long time, unlink and upload again.
- **Unlink** only removes the link inside ArcReel. It deletes neither the ArcReel image nor the asset in the provider's library. Rename or delete provider assets in the provider console. You can upload again after unlinking.
- On the first upload, ArcReel creates an asset group named `ArcReel` in the account's project. The provider requires signing an authorization in its console before an asset group can be created; until then, uploads fail with the provider's error code.

Relay copies in the bucket all live under the `arcreel-relay/` prefix and are cleaned up automatically by ArcReel. Once an upload has a result, its copy is kept for at least 1 hour after it was written and then deleted; if there is never a result, the copy is kept until 1 hour after the download URL expires. Failed deletions are retried automatically, and cleanup never touches other objects in the bucket. When deleting the connection or changing the bucket or Endpoint, you can delete these copies immediately or leave them in the bucket. See [Save New Images to Object Storage](#remote-image-storage).

When an image is already [saved to object storage](#remote-image-storage), no relay copy is written; the download URL is signed for that image in the bucket directly. If you delete the image in ArcReel and purge it while the upload is still in progress, the image in the bucket is kept for the same period so the provider can still fetch it, and is deleted only after that period if nothing else uses it.

## Select an Existing Image from an Asset Library {#select-from-asset-library}

Choose **Select from provider asset library** from the more-actions menu on a project asset card or on a derivative in the character details to view the name, type, and availability of the assets in each account, and select an image that is already in the library to use as this asset image or derivative image. Selecting does not require object storage.

- Only image assets whose status is **Available** can be selected. ArcReel queries the asset from the provider, retrieves the original image from the access URL the provider returns, and saves it as a new version in the same way as an uploaded image: the new version appears in the version history, can be previewed and edited, and you can switch back to earlier versions.
- The new version is automatically linked to the selected asset with an **Available** link that records the asset library account's site, region, and project. Earlier versions do not inherit this link.
- If the original image cannot be retrieved, nothing is imported, for example when the asset was deleted on the provider side, the access URL has expired, or the network dropped. The original image must be PNG, JPEG, or WebP and no larger than 30 MB. When an import fails, the image keeps its previous version and no link is created; try again later or select another asset.
- Imports never compress or scale the original image, so the saved version matches the provider asset: asset images keep the original image bytes; derivative images are always saved as PNG, so a PNG original is saved as is and a JPEG or WebP original is converted to PNG with its original pixels.

## Save to the Global Asset Library and Apply to Other Projects {#asset-library-across-projects}

When you save a project asset to the global asset library, or apply an asset from the global asset library to another project, the **Available** links of the current version of its asset image and derivative images go along with the images. They still belong to the original asset library account, site, region, and project.

- What goes along is a copy of each link, held independently by each project or by the global asset library: unlinking or replacing the image in one place does not affect the other copies.
- Generation still decides whether a link applies by the inference credential actually used for the call. If that credential is bound to a different account, the copied link does not apply and the image content is sent as usual.
- Only **Available** links are copied. Tasks that are still uploading or that failed are not copied; upload again in the target project if needed.
- After you delete the source project or asset, the global asset library and the projects it was applied to keep working, and the assets in the provider asset library are not deleted.

## Project ZIPs and Provider Asset Links {#asset-library-in-project-zip}

When you export a project ZIP, the **Available** links of the image versions in the package go along with the images. They still belong to the original asset library account, site, region, and project. After you import the ZIP into another ArcReel instance, if the asset library account bound to the inference credential belongs to the same cloud account as the original account, generation uses these assets directly, without uploading again.

- An imported link is used only when the account bound to the inference credential belongs to the same cloud account as the original account and has the same site, region, and project. Otherwise the link is kept but not used, and the image content is sent as usual. Whether an asset can be used is ultimately decided by the provider for each account.
- Only **Available** links are included. Tasks that are still uploading, waiting for confirmation, or that failed are not included; upload again in the target project after import.
- A "current version only" ZIP includes links only for the versions that ship in the package. Images whose version records do not ship in the package (such as generated asset images) carry no links.
- The ZIP contains no asset library Access Keys, signed URLs, or object storage details. If an image in the package has been modified, its link is not imported, and the image is imported as usual.

## Use the Asset Library During Generation {#generate-with-asset-library}

Before a reference-to-video request is submitted, ArcReel determines the inference credential the video channel actually uses for this call, finds the asset library account that credential is bound to with a confirmed binding, and then checks, image by image, whether the currently selected version of each reference image has a link in that account. Links created by uploading and by importing follow the same rules.

- **The link is available**: the reference image is sent as an `asset://` asset reference, and the other reference images are sent as image content as usual. The number and order of reference images and the image numbering in the prompt stay the same.
- **No applicable link**: the image content is sent as usual. This applies when the inference credential has no binding or its binding needs to be confirmed again, when the current version has never been uploaded or imported, and when the model does not accept asset references.
- **The link is not yet available or ingestion failed**: when the link is in the **Waiting to upload**, **Submitting**, **Provider processing**, **Result unconfirmed**, or **Failed** state, ArcReel does not fall back to sending the image content and does not submit a request to the provider. The pre-submission check, the batch generation preview, and the cost estimate already report this problem and give no estimate for that unit. During execution, ArcReel checks again against the images actually sent; if the state changes after submission, generation fails during execution. The message names the image by its number: you can generate once ingestion is available; when ingestion failed, the message includes the provider's error code. You can also unlink and send the plain image instead.

Volcano Engine Ark and BytePlus accept asset references for reference images through the Seedance 2.0 and 2.5 call endpoints. ArcReel checks the selected endpoint for both built-in and custom providers. The model ID alone does not determine support, and the Seedance 1.x endpoint does not support asset references. First frames, end frames, storyboard images, and images sent for image generation still use the image content. After you switch inference credentials, ArcReel checks the new credential's binding and applies the links for that account.

When the provider rejects a request because an input image failed moderation (error codes that start with `InputImageSensitiveContentDetected`, for example `InputImageSensitiveContentDetected.PrivacyInformation`), ArcReel does not upload automatically, does not retry generation, and does not switch providers. The error shows the provider's error code and suggests uploading the related asset image to the provider asset library and generating again once ingestion is available. The error names the image only when the request contains a single reference image and no first or last frame; when the provider error does not identify the image, ArcReel does not guess. When the rejected image was already sent as an asset reference, the error instead suggests checking that asset in the provider console.

## Use the Asset Library in a Conversation {#asset-library-in-agent}

The ArcReel Agent and external Agents connected through remote MCP can perform the same asset library operations, using the same links as the asset cards:

- check the links and ingestion status of an image's current version, and the available asset library accounts;
- browse the existing assets in an asset library account, and save the image you choose as a new version of the asset image;
- upload a specific image to the asset library when you explicitly ask for it;
- unlink after you agree; neither the ArcReel image nor the provider asset is deleted.

The Agent uploads only when you explicitly ask. After a provider rejects a generation during moderation, the Agent relays the reason and explains that you can upload; you decide, and it does not upload or generate again automatically. After an upload, the Agent receives the ingestion job right away and does not wait for provider review; you can ask the Agent to check the status later. Asset library accounts, object storage, and inference credential bindings are still configured in the **Asset library & storage** section. The Agent cannot see their keys, and it cannot rename or delete assets in the provider asset library.

## Save New Images to Object Storage {#remote-image-storage}

By default, the object storage connection is only used to relay images when adding them to an asset library. To keep new images in the bucket and leave only a cache that can be cleaned up on this machine, choose **Object storage** under **Save new images to** in the **Asset library & storage** section. This is a global setting for the instance. It defaults to **Local**, and there is no per-project or per-image option.

**Minimum setup**

1. Configure the object storage connection and use **Test connection** to confirm that the credentials can reach the bucket.
2. Under **Save new images to**, choose **Object storage**. The choice takes effect as soon as it is selected. It cannot be chosen while no connection is configured.

The connection's Access Key needs permission to upload, read (including reading object metadata), and delete objects in this bucket. **Test connection** only checks that the bucket can be accessed, not these object permissions. The delete permission is used to clean up relay copies and to remove image objects that are no longer used.

**What the setting affects**

- The setting applies only to image versions saved from now on: generated and inpainted images, grid images and the storyboard images split from them, manually uploaded asset images, storyboard images, derivatives and grid images, images selected from an asset library, imported project ZIPs, and images saved to the global asset library or applied to a project.
- Existing images and earlier versions are not moved and are still read from where they are. Switching back to an earlier version or restoring from the trash keeps that version's original location and does not count as saving a new image.
- Switching back to **Local** affects only images saved afterwards; images already in the bucket are still read as usual. A project can contain local and remote images at the same time.
- The following images have no version history and are always saved locally: storyboard end frames, character and product original images, and the project's style reference image. Video and audio are also always saved locally.

**Relay copies and image objects**

Each prefix in the bucket identifies a different kind of object with its own cleanup rules:

| Prefix | Content | Cleanup |
| --- | --- | --- |
| `arcreel-relay/` | Relay copies for asset library uploads | Deleted in the background once the upload has a result or the download URL has expired; see [Upload Images to an Asset Library](#upload-to-asset-library) |
| `arcreel-materials/` | Images saved to object storage, named by content, so identical images are stored once | Kept as long as version history, the trash, a deleted project, the global asset library, another project, or an upload in progress still uses them; deleted by ArcReel once nothing uses them (for example, after permanently deleting from the trash) |

Do not delete or overwrite objects under `arcreel-materials/` in the TOS console; otherwise the corresponding images can no longer be previewed, generated from, or exported. Relay cleanup never touches these objects.

**Local cache**

Each remote image has a local cache copy that preview, generation, and image processing use directly. The cache is only a copy: after it is cleaned up, the image is fetched from object storage again when needed and its content is verified. Opening the workspace or viewing status does not trigger downloads.

- ArcReel cleans up cache files that have been idle for more than 24 hours once an hour in the background.
- To free space immediately, call `POST /api/v1/object-storage/work-cache/cleanup`. The request body can include `min_idle_seconds` to clean up only cache files idle for longer than that many seconds; the default is `0`. The response returns the number of files removed (`removed`), the bytes freed (`freed_bytes`), and the number of files kept because they are in use (`kept_in_use`).
- Cleanup only deletes cache files that are confirmed to be saved in object storage and match the remote content. Local images, files in use, and objects in the bucket are not affected.

**Backup and migration**

- The content of remote images lives only in the bucket, so a backup of the ArcReel data directory does not include it. Keep the bucket as well once you enable remote storage. ArcReel deletes image objects that are no longer used; to be able to restore an older backup, also back up the objects under `arcreel-materials/` in the bucket, or enable versioning on the bucket. See [Backup and Restore](../ops/deployment.md#backup-and-restore).
- To move to another ArcReel instance, export a project ZIP. Before exporting, ArcReel fetches remote images whose cache has been cleaned up, so the ZIP contains the actual image content and no bucket name or access keys. The other instance saves these images again according to its own **Save new images to** setting.

**Changing or deleting the connection**

- While a project, the trash, or the global asset library still uses images in the bucket, or an image is being saved, you can update only the Access Key. You cannot change the bucket, Endpoint, or region, or delete the connection. Images with no references and images used only by ingestion jobs do not block these changes.
- While **Save new images to** is **Object storage**, the connection cannot be deleted; switch it back to **Local** first.
- If the connection is changed while an image is being saved, that save fails and the previous image is kept; retry to save it to the new bucket.
- While a project is being deleted or replaced by an import, images saved to that project fail; the project's existing images are not affected.
- When you delete the connection or change the bucket or Endpoint, **Also delete files ArcReel wrote to the bucket that are awaiting deletion** is selected by default. ArcReel uses the current credentials to immediately delete unreferenced images, images used only by ingestion jobs, and all relay copies, including those still within their retention period. If you leave the box selected when changing the bucket or Endpoint, you must confirm before saving. Changing only the region does not show this option or change the retention and cleanup rules.
- Clear the checkbox to leave the files in the bucket and remove their ArcReel records. If any deletions fail with the box selected, ArcReel reports the count and reason once, applies the connection change, and removes the records. Use the original bucket’s console to remove any files left behind. ArcReel does not retry, and other files in the bucket are unaffected.
- The dialog shows how many ingestion jobs are still pending. Once the connection no longer points to the old bucket, affected jobs fail. The original images remain in ArcReel. Unlink them to upload them again.

**Troubleshooting**

- **"The image could not be saved to object storage"**: the new version does not appear, and earlier versions, artifacts, and files are unchanged. Use **Test connection** to check the connection, check the Access Key's upload permission and the network, and try again. You can also switch back to **Local** and keep working. If this message appears while importing a project ZIP, the import did not write any project; check the connection and import again.
- **The region, Endpoint, or bucket is wrong and images keep failing to save**: first switch **Save new images to** back to **Local**, then edit the connection to correct it, or delete the connection and configure it again. Images that failed to save are not used anywhere and do not block the change. When you correct the Endpoint or bucket, the dialog offers the cleanup option described above. If the original Endpoint is unreachable, ArcReel reports the network failure once and clears the records. Objects that were never written to the bucket need no action. Clean up any objects that were written to the original bucket but could not be deleted in the TOS console. Do not bulk-delete objects by the `arcreel-materials/` prefix: if the bucket is the same before and after the correction, it contains images that are in use.
- **"The image could not be retrieved from object storage" during preview, generation, or export**: check the network and read permissions first. The project trash only keeps a reference to the image in the storage bucket. If the object was deleted or overwritten, restore the original contents to the same object key from a bucket backup or an earlier bucket version, then retry. If you cannot recover the original contents, use an image version you can still access, or generate or upload the image again.
- **The image is saved in the original bucket, but the current connection was deleted or points to another bucket**: the connection's bucket or Endpoint no longer matches the settings used when the image was saved. Change the connection back to the original bucket and Endpoint to read it again.
- **New images are set to be saved to object storage, but no connection is configured**: configure the connection, or switch **Save new images to** back to **Local**.
- **Saving images is slow**: images are uploaded to object storage while the version is saved, and the new version appears only after the upload is confirmed. During the upload, other image saves in the same project wait in line; this is more noticeable with large images or slow networks. When importing a project ZIP, images are uploaded one by one within the import request.
