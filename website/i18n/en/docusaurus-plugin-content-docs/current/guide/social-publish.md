---
id: social-publish
title: Publish to Social Platforms
sidebar_position: 6
update_docs: fact-check
---

# Publish to Social Platforms {#social-publish}

Once a video is finished you can send it to TikTok, YouTube, Instagram, X, LinkedIn and more straight from the player, instead of downloading it and uploading it to each platform by hand. Delivery goes through [Upload-Post](https://upload-post.com), which wraps every platform's publishing API behind one credential.

What gets published is always the exact rendition the player has selected — the same rule the editable bundle download and the Jianying draft export follow. Switch versions and the next publish sends the new one.

## Prerequisites {#prerequisites}

- At least one finished shot/unit video
- An Upload-Post account where you have:
  1. Connected the social accounts you want to post to
  2. Created a **profile** that groups those accounts
  3. Generated an **API key**

## Configure the credentials {#configure-credentials}

Open **System Settings → Distribution** and fill in two fields:

- **API key**: the key generated in the Upload-Post dashboard. It is stored only in the local database, and reading the configuration returns nothing but whether a key is set — not even a mask, since a few leading and trailing characters are enough to identify which key it is.
- **Profile name**: the Upload-Post profile name (not a social handle). It selects which group of connected accounts receives the post.

Leave **Service address** empty to use the official endpoint. Only a self-hosted proxy or a staging environment needs a value, and it must be an https URL without credentials or a query string.

After saving, click **Load accounts** to confirm every account is present and none is flagged *needs reauthorization* — a flagged connection always fails to publish and has to be reauthorized in Upload-Post first.

## Publish a video {#publish}

1. Open the player for any finished video on the canvas
2. Click the **Publish** button in the bottom-right corner
3. Tick the target platforms: the list only shows platforms the profile has connected *and* that accept video uploads
4. Fill in the title; the body is optional
5. To schedule, pick a time — it is read in this machine's timezone; leave it empty to publish now
6. Click **Publish**

Once the request is accepted the dialog switches to a progress panel that reports each platform separately, with a link to the post for every platform that succeeds.

## Platform differences {#platform-notes}

- **Title**: required by YouTube, optional elsewhere, but every platform uses it as the main caption.
- **Body**: only LinkedIn, Facebook, YouTube and Pinterest use it; other platforms ignore it.
- **Unconnected platforms**: if the profile has no account for a platform, the whole submission does not fail — that platform is reported as *not connected, skipped* and the rest publish normally.
- **AI disclosure**: every ArcReel video is model-generated, so submissions always carry the cross-platform AI content flag (TikTok's `is_aigc`, X's `made_with_ai`, and so on). It is not an option you can turn off.

## Troubleshooting {#troubleshooting}

**"Social distribution is not configured"**
The API key or the profile name is empty. Both are required before publishing.

**"Upload-Post rejected these credentials"**
The API key is invalid or expired; generate a new one in the Upload-Post dashboard.

**"This month's publishing quota is used up"**
Upload-Post caps monthly publishes per plan. Upgrade the plan or wait for the quota to reset.

**A platform stays on "processing"**
Transcoding and review times differ per platform and are noticeably longer for long videos. Closing the dialog does not stop the background delivery, but it does clear the local progress panel — reopening gives you a fresh submission form.

:::warning Publishing again before the previous delivery finishes posts it twice
Closing the dialog only stops you watching the progress; the delivery keeps running on the server.
Reopening and publishing again is a **brand-new delivery** — the upstream does not recognise it as the
same one, so the same video appears twice on the target platforms, and social platforms cannot roll
that back. Wait for the previous delivery to reach a terminal state before publishing again.
:::

**"The selected media is unavailable"**
The version file the player selected was cleaned up or renamed. Regenerate it, or switch to a version that still exists, and publish again.
