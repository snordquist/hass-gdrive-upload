"""Expose files uploaded by gdrive_upload as a Home Assistant media source.

Identifier scheme:
- ``""`` — the root, resolved on demand to ``ROOT_FOLDER_PATH`` in Drive.
- ``folder:<drive_id>`` — a subfolder, expandable.
- ``v:<drive_id>`` — a video file, playable.
- ``i:<drive_id>`` — an image file, playable.

Playback URL is an HA-internal proxy (see view.py) so the browser uses cookie
auth and the bytes are streamed inline regardless of the file's share state.
"""

from __future__ import annotations

import logging

from homeassistant.components.media_player import MediaClass
from homeassistant.components.media_source.error import Unresolvable
from homeassistant.components.media_source.models import (
    BrowseMediaSource,
    MediaSource,
    MediaSourceItem,
    PlayMedia,
)
from homeassistant.core import HomeAssistant

from .api import DriveApi, DriveApiError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

# Drive folder we expose. Scoped to the doorbell sub-tree so unrelated uploads
# (e.g. logs the user may decide to push via the upload service later) do not
# end up in a gallery card that expects timestamp-named media filenames.
ROOT_FOLDER_PATH = "HomeAssistant/Doorbell"

_PREFIX_FOLDER = "folder:"
_PREFIX_VIDEO = "v:"
_PREFIX_IMAGE = "i:"


async def async_get_media_source(hass: HomeAssistant) -> MediaSource:
    """Return the gdrive_upload media source (called by HA at startup)."""
    return GdriveUploadMediaSource(hass)


def _get_api(hass: HomeAssistant) -> DriveApi:
    """Resolve the live DriveApi instance from the loaded config entry."""
    entries = list(hass.data.get(DOMAIN, {}).values())
    if not entries:
        raise Unresolvable("gdrive_upload integration is not configured")
    return entries[0]


def _is_folder(item: dict) -> bool:
    return item.get("mimeType") == "application/vnd.google-apps.folder"


def _identifier_for(entry: dict) -> str | None:
    """Return an identifier for a Drive list entry, or None if not browsable."""
    if _is_folder(entry):
        return f"{_PREFIX_FOLDER}{entry['id']}"
    mime = entry.get("mimeType", "")
    if mime.startswith("video/"):
        return f"{_PREFIX_VIDEO}{entry['id']}"
    if mime.startswith("image/"):
        return f"{_PREFIX_IMAGE}{entry['id']}"
    return None


class GdriveUploadMediaSource(MediaSource):
    """Media source backed by the Drive folder our uploads live in."""

    name: str = "Google Drive Upload"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a playable item to an HA-internal proxy URL."""
        ident = item.identifier or ""
        if ident.startswith(_PREFIX_VIDEO):
            file_id = ident[len(_PREFIX_VIDEO) :]
            mime = "video/mp4"
        elif ident.startswith(_PREFIX_IMAGE):
            file_id = ident[len(_PREFIX_IMAGE) :]
            mime = "image/jpeg"
        else:
            raise Unresolvable(f"Not a playable identifier: {ident!r}")
        return PlayMedia(f"/api/gdrive_upload/stream/{file_id}", mime)

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse a folder. Identifier 'folder:<id>' or empty for root."""
        api = _get_api(self.hass)
        identifier = item.identifier or ""

        if not identifier:
            try:
                root_id = await api.ensure_folder(ROOT_FOLDER_PATH)
            except DriveApiError as err:
                raise Unresolvable(f"Drive root resolve failed: {err}") from err
            return await self._browse_folder(api, root_id, ROOT_FOLDER_PATH, is_root=True)

        if identifier.startswith(_PREFIX_FOLDER):
            folder_id = identifier[len(_PREFIX_FOLDER) :]
            return await self._browse_folder(api, folder_id, "")

        raise Unresolvable(f"Cannot browse non-folder identifier: {identifier!r}")

    async def _browse_folder(
        self, api: DriveApi, folder_id: str, name: str, is_root: bool = False
    ) -> BrowseMediaSource:
        try:
            entries = await api.list_folder(folder_id)
        except DriveApiError as err:
            raise Unresolvable(f"Drive list failed: {err}") from err

        # Partition into folders + media, preserving the createdTime-desc order
        # we already requested from the Drive API. (Re-sorting alphabetically
        # would invert chronological order at year boundaries.)
        folders: list[BrowseMediaSource] = []
        files: list[BrowseMediaSource] = []
        for entry in entries:
            ident = _identifier_for(entry)
            if ident is None:
                continue  # not browsable / not media
            mime = entry.get("mimeType", "")
            if ident.startswith(_PREFIX_FOLDER):
                folders.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=ident,
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="",
                        title=entry["name"],
                        can_play=False,
                        can_expand=True,
                        thumbnail=None,
                    )
                )
            else:
                files.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=ident,
                        media_class=(
                            MediaClass.VIDEO
                            if mime.startswith("video/")
                            else MediaClass.IMAGE
                        ),
                        media_content_type=mime,
                        title=entry["name"],
                        can_play=True,
                        can_expand=False,
                        thumbnail=entry.get("thumbnailLink"),
                    )
                )

        children = folders + files
        children_media_class = (
            MediaClass.DIRECTORY if folders else (MediaClass.VIDEO if files else MediaClass.DIRECTORY)
        )

        # Root identifier stays empty so HA re-resolves the path on every visit
        # (a path-based root is more robust than a cached id across restarts).
        node_identifier = "" if is_root else f"{_PREFIX_FOLDER}{folder_id}"
        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=node_identifier,
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=name or "Google Drive",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=children_media_class,
        )
