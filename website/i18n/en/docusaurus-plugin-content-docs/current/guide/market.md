---
id: market
title: Market
sidebar_position: 5
update_docs: fact-check
---

# Market {#market}

The market lets you browse call endpoints that others have already adapted, from one or more market sources, and install them as local custom call endpoints after confirmation. Installed endpoints can then follow upstream updates. This page covers managing market sources, what to check before installing, and updating and uninstalling. For the concepts behind call endpoints and how to test them, see [Provider and Model Configuration](./providers.md#custom-providers).

## 1. What Are the Market and Market Sources {#what-is-market}

- **Market**: the "Market" section in the Configuration group of Settings, placed after "Endpoints". Browsing and installing run only on your local ArcReel and the market source repositories. They do not depend on any central service, and no account is required. Install counts and ratings come from an official service you can turn off; see [Install Counts, Ratings, and the Official Service](#official-service). In the first release, the only market entry type is call endpoints.
- **Market source**: a GitHub repository (or an equivalent `https://` direct link) whose root index file `arcreel-market.json` lists its market entries. The ArcReel backend fetches the index and definitions on your behalf and only accesses raw file URLs.
- **Official market source**: [`ArcReel/arcreel-market`](https://github.com/ArcReel/arcreel-market) ships preconfigured. It can be disabled, renamed, and reordered, but not deleted. Every other source you add yourself is a third-party market source.
- **Market entry**: an installable item in an index, whose payload is a call endpoint definition. An entry is uniquely identified by its slug within its market source. The same slug in different market sources does not mean the same definition; such entries are displayed side by side.

The market is a user feature: the embedded Agent does not browse or install market entries for you.

## 2. Manage Market Sources {#manage-sources}

Click "Manage market sources" in the header of the "Market" section to open the dialog. The list order is the display order of the entry grid. Each row shows the status, display name (the official source carries an "Official" badge), address, and last successful refresh time.

### 2.1 Add a Market Source {#add-source}

Enter any of the following forms in "Market source address" at the bottom of the dialog, then click "Add":

| Form | Example |
|---|---|
| `owner/repo` | `ArcReel/arcreel-market` |
| `owner/repo@ref` (branch, tag, or commit) | `ArcReel/arcreel-market@main` |
| GitHub repository URL (optionally with `/tree/<ref>`) | `https://github.com/ArcReel/arcreel-market` |
| `https://` direct link ending in `arcreel-market.json` | `https://raw.githubusercontent.com/ArcReel/arcreel-market/main/arcreel-market.json` |

GitHub forms without a ref follow the repository's default branch. `http://` URLs, local paths, and `git@` addresses are not accepted, and a market source that is already registered cannot be added again.

Adding a source fetches its index immediately. If the fetch fails or the index is invalid, the source is rejected and the reason is shown. The display name defaults to the name in the index and can be edited directly in the list after adding.

A fixed third-party notice is shown below the add form:

> **Adding a third-party market source**
>
> This market source is maintained by a third party and its content has not been reviewed by ArcReel. Make sure you trust the source before installing anything from it.

A market entry determines where your API key is sent, so only add market sources you trust.

### 2.2 Enable, Disable, Reorder, Refresh, and Delete {#source-actions}

- **Enable toggle**: a disabled market source stays registered, but its entries are hidden. Endpoints installed from it show "Unavailable in market" and keep working.
- **Reorder**: drag the handle at the start of a row, or focus the handle and press the up and down arrow keys.
- **Rename**: edit the display name directly in the row.
- **Refresh**: click the refresh icon for a single source. "Refresh all" in the section header refreshes all enabled market sources in parallel.
- **Delete**: click the delete icon. The delete button is unavailable for the official market source; disable it instead if needed.
- **Homepage**: when the index provides a homepage, the row shows an external link.

When you open the "Market" section, enabled market sources last refreshed more than 1 hour ago are refreshed automatically in the background, while the page shows cached content first. If a refresh fails, the last successful snapshot is kept, and a banner above the grid lists each affected source's status, error, and snapshot time. Market source statuses are "OK", "Not refreshed yet", "Unreachable", "Invalid index", and "Requires a newer ArcReel".

### 2.3 GitHub Raw Proxy Prefix {#github-proxy-prefix}

If your deployment cannot reach `raw.githubusercontent.com` directly, find "GitHub raw proxy prefix" in the "Models" section of Settings. When set, ArcReel prepends the prefix to every `raw.githubusercontent.com` address, covering index, definition, and icon fetches. Leave it empty to connect directly.

This is a single global setting, empty by default, and ArcReel does not preconfigure any proxy address. Once set, failed requests do not fall back to a direct connection. The proxy can see the requests passing through it, so only use a proxy you trust.

## 3. Browse and Install {#browse-and-install}

You can also enter the "Market" section by clicking "Get from market" in the "Endpoints" section.

### 3.1 Browse Entries {#browse-entries}

The entry grid lists entries from all enabled sources in market source order, without grouping by source. Each card shows the icon, name, `author · vversion`, description, market source (entries from the official market source also show install counts and ratings next to it), and a primary button in the lower-right corner ("Install", "Update", or "Installed").

- **Search**: filter by name, author, and description.
- **Entry type**: only "Endpoints" is currently available; "Prompts" and "Style templates" are marked as coming soon.
- **Filter by source**: one filter per enabled market source, multi-select, all selected by default.
- **Installed only**: show only installed entries.

When an entry requires a newer app version, the whole card is dimmed, its button is disabled, and it is labeled "Requires ArcReel ≥ x.y.z". Upgrade ArcReel before installing it.

### 3.2 Confirm Installation {#confirm-install}

Click a card or "Install" to open the installation confirmation dialog. Check each part before installing:

1. **Header**: name, author, version, market source, and homepage. For entries from a third-party market source, the dialog shows "This source has not been reviewed by ArcReel".
2. **Validation**: the definition's errors and warnings. "Confirm installation" is disabled, with the reason shown, when the index and definition do not match, validation reports errors, or the app version is lower than "Requires ArcReel ≥ x.y.z".
3. **Hints**: the base URL and models suggested by the author. They are only suggestions, for reference when you later create a provider.
4. **Trust**: the most important part to check before installing.
   - **Credentials sent to**: the original `submit.url` from the definition, the address your API key is sent to with the submit request;
   - **Polling URL**: the original `poll.url` from the definition;
   - **Authentication (original auth JSON)**: the original `auth` JSON from the definition, expanded by default, which describes how and where in the request your API key is sent.

   Confirm that these addresses belong to the provider or gateway you intend to use before continuing.

If an endpoint from the same author with the same name already exists locally, the dialog lists those endpoints for you to choose from:

- **Overwrite**: select an existing endpoint and overwrite it in place with the market definition. The endpoint key and attached models stay the same, and the endpoint follows this market entry from then on.
- **Create a copy**: create a new call endpoint; existing endpoints are unaffected.
- **Installed from X**: the endpoint already follows an entry from another market source. Its source is shown, but it cannot be selected for overwrite.
- If you decide not to install, click "Cancel" in the footer.

After you click "Confirm installation", the success message offers "Create provider with this endpoint" and "Open endpoint".

Only endpoints installed through the market count as "Installed". A definition imported from a local file is not bound to a market entry and does not count as installed, even if the author and name match.

## 4. Updates and Local Changes {#updates-and-local-changes}

Installed endpoints carry two-axis status badges, shown on market cards, in the installation confirmation dialog, and in the endpoint detail header of the "Endpoints" section:

| Market status | Meaning |
|---|---|
| Installed | The installed version matches the market source's current version |
| Update available | The version in the market source differs from the installed version (including when the author rolls the version back) |
| Unavailable in market | The market source is disabled or deleted, or the entry has been removed from the index; shown only in endpoint details, and the endpoint itself keeps working |

"Modified" is a separate axis, independent of market status: it appears when the current definition differs from the installed definition. You can edit an installed endpoint directly, like any other custom call endpoint, and editing does not unbind it from the market entry.

The metadata row of the endpoint detail header shows "From market <market source display name>". When the market source is disabled, "(market source disabled)" is appended. When it is deleted, the row shows the canonical form of the market source address instead, followed by "(market source deleted)".

### 4.1 Run an Update {#run-update}

When the status is "Update available", click "Update" on the market card or "Update" in the endpoint detail action row to open the same confirmation dialog:

- The header shows "Installed vX → Market vY". Validation, hints, and trust information are the same as during installation, so check where credentials are sent again before updating.
- When the endpoint carries the "Modified" badge, or its detail has unsaved changes, "Your local changes will be overwritten" appears above the primary button. Click "Export current definition first" to keep a copy.
- After you click "Update to vY", the market definition overwrites the local definition in place. The endpoint key and attached models stay the same, and newly submitted tasks use the new definition immediately.

An update discards your local changes to the endpoint. To keep your own version long term, export the current definition first, then import it with "Import" in the "Endpoints" section using "Import as a copy". Imported endpoints do not follow market entries.

## 5. Uninstall {#uninstall}

Uninstalling means deleting the call endpoint. The following two entry points have the same effect:

- Click "Uninstall" in the footer of an installed entry's installation confirmation dialog; it runs immediately.
- Click "Delete" in the endpoint detail of the "Endpoints" section; it runs after confirmation.

The endpoint and its installation record are deleted together. If models still use the endpoint, it cannot be deleted; the interface lists the models that reference it, so switch those models to another endpoint before uninstalling.

Disabling or deleting a market source does not uninstall installed endpoints.

## 6. Install Counts, Ratings, and the Official Service {#official-service}

The official service is an overlay run by the ArcReel team that records install counts and ratings for entries from the official market source. It takes no part in browsing or installing: when it is turned off or unreachable, the market works as usual and simply shows no numbers. The frontend only talks to your local server, which sends every request to the official service on its behalf.

### 6.1 Install Counts and Ratings {#installs-and-ratings}

- Cards of entries from the official market source show install counts and ratings next to the market source: the download icon marks the install count (a rounded display value from the official service), and the star marks the average rating and the number of ratings. Until there are enough ratings, only the number of ratings appears. Entries from third-party market sources show no numbers and are never sent to the official service.
- In the header of the install confirmation dialog, you can rate an entry from 1 to 5 stars and change the rating anytime. Only installed entries can be rated. If the official service has no record of this ArcReel installing the entry (for example, because the official service was off at install time), you are told the entry cannot be rated.

### 6.2 What Is Reported {#what-is-reported}

After you install an entry from the official market source for the **first** time, the local server reports it to the official service once: the market source, entry type, slug, entry version, and app version, with the instance ID in a request header. Updates, uninstalls, and installs from third-party market sources are never reported. A failed report does not affect the installation and is not retried.

The instance ID is a randomly generated UUID, created the first time it is needed and stored locally. It is not tied to your machine or any account, and the official service stores only its hash.

The first time you open the "Market" section, a notice at the top explains what is reported. Click "Got it", or click "Turn off official service" right away.

### 6.3 Turn Off the Official Service and Reset the Instance ID {#turn-off-official-service}

In the "Official service" card under Settings › About:

- **Use the official service**: when turned off, the market page no longer shows install counts, ratings, or the notice, and the local server sends nothing to the official service.
- **Reset instance ID**: deletes the current instance ID; a new one is created the next time the official service is contacted. The official service then treats this ArcReel as a new instance, and earlier install reports and ratings are no longer linked to it. Entries installed before the reset must be reinstalled before you can rate them again.

For self-hosted deployments, set the environment variable `ARCREEL_OFFICIAL_SERVICE_URL` to empty to turn off the official service entirely; see [Deployment](../ops/deployment.md).

### 6.4 Share to the Official Market {#share-to-official-market}

When the official service is on, your own declarative endpoints under "Endpoints" have a "Share to official market" button in the header. Save any unsaved changes first: the saved definition of the endpoint is what gets submitted.

1. The dialog first shows the local validation result, which follows the same rules as the official market's pre-check and CI. Any issues are listed one by one, and you cannot submit until all of them are fixed.
2. Fill in the **suggested slug**: the entry directory name, derived from the endpoint name by default, with lowercase letters, digits and hyphens only. Reviewers may adjust the final slug before merging.
3. Optionally enter your **GitHub username** to be @-mentioned in the PR, and attach a square icon (PNG, WebP or SVG, at most 64 KB).
4. After you click "Submit", the official service opens a PR on the official market source repository, and the review happens on GitHub: merged means accepted, closed means rejected.

Endpoint definitions contain no credentials; the `auth` section holds only placeholders. The submitted content appears publicly in the PR. Submitting the same slug again before the PR is merged pushes your changes to the same PR; submitting after the PR is merged or closed opens a new PR for review.

The endpoint header and "My submissions" in the "Market" section show the status of each endpoint's latest submission (In review / Accepted / Rejected) with a PR link, refreshed once when you open the page. When the official service is unreachable, the last known status is shown.

## 7. Contribute and Host Your Own Market Source {#contribute-and-host}

The official market source repository documentation is authoritative for the contribution workflow, content guidelines, and hosting your own market source:

- [Official market source README](https://github.com/ArcReel/arcreel-market/blob/main/README.en.md): browsing entries and hosting your own market source;
- [Official market source CONTRIBUTING](https://github.com/ArcReel/arcreel-market/blob/main/CONTRIBUTING.md): contribution workflow and content guidelines.

"Read the contribution guide" at the end of the "Market" section, and "Contribute to market" next to "Export" in endpoint details, also point to the CONTRIBUTING document above.
