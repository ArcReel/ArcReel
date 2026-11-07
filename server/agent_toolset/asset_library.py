"""Provider asset-library declarations: query image links, browse libraries, upload the current image, select existing assets, and release links.

Remote renaming and deletion are not declared: the provider console handles them. Library accounts, object storage, and inference-credential bindings are configured only in settings; tools never access raw secrets (``docs/adr/0114``)."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, Exempt, ToolDeclaration
from server.media_tools.provider_assets import (
    ListProviderLibraryAssetsRequest,
    ProviderAssetLinksRequest,
    ReleaseProviderAssetLinkRequest,
    SelectProviderLibraryAssetRequest,
    UploadToProviderAssetLibraryRequest,
    get_provider_asset_links,
    list_provider_library_assets,
    release_provider_asset_link,
    select_provider_library_asset,
    select_summary,
    upload_summary,
    upload_to_provider_asset_library,
)

_LINK_FIELDS = (
    "Each link contains id, account_id, scope_key, version, state (pending upload, submitting, processing at provider, "
    "uncertain submission result awaiting recovery, active usable, failed), usable, provider_asset_id, error_code, error_message"
    " and polling (false with unsettled state means background automatic querying has stopped)."
)

GET_PROVIDER_ASSET_LINKS = ToolDeclaration(
    name="get_provider_asset_links",
    description=(
        "Query the currently selected image version’s provider-asset-library links and ingestion state (Volcengine Ark / BytePlus ModelArk), the same records as Upload to provider asset library on Web asset cards. Returns content_version (current version, null without image), object_storage_configured (whether upload object storage is configured), accounts (available library accounts: id, scope_key, name, site, region, project_name), and links. A link belongs to an account only when scope_key matches. Replacing account Access Keys with another cloud account leaves old links unmatched, unusable for generation, and only releasable. Links belong to specific content versions: a new image version does not inherit old links; switching back uses still-valid links for the old version. Generation uses only active links; processing or failed links explicitly fail reference-video generation rather than silently sending an ordinary image. recheck=true asks the background worker to requery processing ingestion tasks immediately and still returns without waiting for the provider. Unsupported resource types return unsupported_resource_type."
        + _LINK_FIELDS
    ),
    request_model=ProviderAssetLinksRequest,
    migration=Exempt(
        "Reads version records and database links; recheck changes only the next database polling time and writes no project files."
    ),
    domain_key="provider_asset_links",
    handler=get_provider_asset_links,
)

LIST_PROVIDER_LIBRARY_ASSETS = ToolDeclaration(
    name="list_provider_library_assets",
    description=(
        "Page through existing provider-library assets in the account’s site, region, and project scope, newest first. Each entry has id, name, asset_type (Image / Video / Audio), status (active / processing / failed / unknown), error_code, and created_at; also returns total, page, page_size. Only Image assets with active status can be selected through select_provider_library_asset. Results exclude provider-signed download URLs. Read-only; changes no links. Missing accounts return asset_library_account_not_found; provider refusal or request failure returns control-plane error codes to relay unchanged to the user."
    ),
    request_model=ListProviderLibraryAssetsRequest,
    migration=Exempt("Reads only the provider asset library, without project-data access."),
    domain_key="provider_library_assets",
    handler=list_provider_library_assets,
)

UPLOAD_TO_PROVIDER_ASSET_LIBRARY = ToolDeclaration(
    name="upload_to_provider_asset_library",
    description=(
        "Upload the currently selected image version to the provider asset library, equivalent to Upload to provider asset library on a Web asset card. Call only when the user explicitly requests uploading this image, with user_requested=true. Provider moderation rejection of generation is not an upload request: relay the reason and explain upload is an option for the user to choose; do not upload independently or regenerate automatically. The upload freezes the version selected at call time; later version changes do not affect ingestion. The image passes through user-owned object storage before submission. Background upload and moderation proceed asynchronously: immediately returns an ingestion task (state=pending), without waiting. Later query get_provider_asset_links; after active, regenerate only according to the user’s wishes; do not repeatedly poll. Missing image version returns provider_asset_upload_no_image, missing object storage provider_asset_upload_needs_object_storage, missing account asset_library_account_not_found; direct the user to settings or the asset card for these. Existing link or ingestion task for this version/account returns provider_asset_link_exists; obtain confirmation, release_provider_asset_link, then upload. Success returns an ingestion task with the same fields as get_provider_asset_links links[]."
    ),
    request_model=UploadToProviderAssetLibraryRequest,
    migration=BLOCKED,
    domain_key="provider_asset_link",
    handler=upload_to_provider_asset_library,
    summary=upload_summary,
)

SELECT_PROVIDER_LIBRARY_ASSET = ToolDeclaration(
    name="select_provider_library_asset",
    description=(
        "Select an existing provider-library image as an asset sheet or character-derivative sheet, equivalent to Select from provider asset library in Web. Call only after the user explicitly selects that asset. Retrieve actual image content first, save it as a new manually uploaded finished-image version and select it as current, then create a usable link for the new version; preserve old versions in history. Retrieval failure, unusable/nonimage assets, or save failure create no link and leave current unchanged. Returns provider_asset_import_not_image for nonimages, provider_asset_import_not_active for unavailable assets, provider_asset_import_download_failed for download failures, or the corresponding *_not_found for missing assets/derivatives. Success returns content_version (new id, version, digest) and link."
    ),
    request_model=SelectProviderLibraryAssetRequest,
    migration=BLOCKED,
    domain_key="provider_asset_selection",
    handler=select_provider_library_asset,
    summary=select_summary,
)

RELEASE_PROVIDER_ASSET_LINK = ToolDeclaration(
    name="release_provider_asset_link",
    description=(
        "Release a provider-asset link, equivalent to Unlink in Web. Only removes the relationship between this image version and provider asset; deletes neither ArcReel images nor provider-library assets. Processing ingestion tasks cease to take effect after release. This version then sends ordinary images during generation and can be uploaded again. Call only with user agreement. Missing link returns provider_asset_link_not_found; already released links return unchanged with selected=false."
    ),
    request_model=ReleaseProviderAssetLinkRequest,
    migration=Exempt(
        "Changes only database links, not project files. Must permit releasing invalid links after migration failure."
    ),
    domain_key="provider_asset_link",
    handler=release_provider_asset_link,
)

ASSET_LIBRARY_TOOLS = (
    GET_PROVIDER_ASSET_LINKS,
    LIST_PROVIDER_LIBRARY_ASSETS,
    UPLOAD_TO_PROVIDER_ASSET_LIBRARY,
    SELECT_PROVIDER_LIBRARY_ASSET,
    RELEASE_PROVIDER_ASSET_LINK,
)

__all__ = [
    "ASSET_LIBRARY_TOOLS",
    "GET_PROVIDER_ASSET_LINKS",
    "LIST_PROVIDER_LIBRARY_ASSETS",
    "RELEASE_PROVIDER_ASSET_LINK",
    "SELECT_PROVIDER_LIBRARY_ASSET",
    "UPLOAD_TO_PROVIDER_ASSET_LIBRARY",
]
