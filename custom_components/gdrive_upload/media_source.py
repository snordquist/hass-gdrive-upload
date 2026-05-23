"""Expose files uploaded by gdrive_upload as a Home Assistant media source."""

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

# The folder we land in when the user opens the media browser.
# Drive's `drive.file` scope only lets us see files we created, so this is
# naturally limited to what gdrive_upload has uploaded.
ROOT_FOLDER_PATH = "HomeAssistant"


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


def _media_class_for(mime: str) -> MediaClass:
    if mime.startswith("video/"):
        return MediaClass.VIDEO
    if mime.startswith("image/"):
        return MediaClass.IMAGE
    if mime.startswith("audio/"):
        return MediaClass.MUSIC
    return MediaClass.URL


class GdriveUploadMediaSource(MediaSource):
    """Media source backed by the Drive folder our uploads live in."""

    name: str = "Google Drive Upload"

    def __init__(self, hass: HomeAssistant) -> None:
        super().__init__(DOMAIN)
        self.hass = hass

    async def async_resolve_media(self, item: MediaSourceItem) -> PlayMedia:
        """Resolve a file's media-source identifier to a playable URL."""
        api = _get_api(self.hass)
        file_id = item.identifier
        if not file_id or file_id.startswith("folder:"):
            raise Unresolvable("Cannot play a folder")
        try:
            meta = await api.get_file(file_id)
        except DriveApiError as err:
            raise Unresolvable(f"Drive get_file failed: {err}") from err
        # Public download URL works for browser <video src> when the file has
        # anyone-with-link permission (our pipeline always uploads with share=true).
        url = f"https://drive.google.com/uc?export=download&id={file_id}"
        return PlayMedia(url, meta.get("mimeType", "application/octet-stream"))

    async def async_browse_media(self, item: MediaSourceItem) -> BrowseMediaSource:
        """Browse a folder. Identifier 'folder:<id>' or empty for root."""
        api = _get_api(self.hass)
        identifier = item.identifier or ""

        if not identifier:
            try:
                root_id = await api.ensure_folder(ROOT_FOLDER_PATH)
            except DriveApiError as err:
                raise Unresolvable(f"Drive root resolve failed: {err}") from err
            return await self._browse_folder(api, root_id, ROOT_FOLDER_PATH)

        if not identifier.startswith("folder:"):
            raise Unresolvable(f"Invalid browse identifier: {identifier!r}")
        folder_id = identifier[len("folder:") :]
        return await self._browse_folder(api, folder_id, "")

    async def _browse_folder(
        self, api: DriveApi, folder_id: str, name: str
    ) -> BrowseMediaSource:
        try:
            entries = await api.list_folder(folder_id)
        except DriveApiError as err:
            raise Unresolvable(f"Drive list failed: {err}") from err

        children: list[BrowseMediaSource] = []
        for entry in entries:
            if _is_folder(entry):
                children.append(
                    BrowseMediaSource(
                        domain=DOMAIN,
                        identifier=f"folder:{entry['id']}",
                        media_class=MediaClass.DIRECTORY,
                        media_content_type="",
                        title=entry["name"],
                        can_play=False,
                        can_expand=True,
                        thumbnail=None,
                    )
                )
                continue

            mime = entry.get("mimeType", "")
            if not (mime.startswith("video/") or mime.startswith("image/")):
                continue
            children.append(
                BrowseMediaSource(
                    domain=DOMAIN,
                    identifier=entry["id"],
                    media_class=_media_class_for(mime),
                    media_content_type=mime,
                    title=entry["name"],
                    can_play=True,
                    can_expand=False,
                    thumbnail=entry.get("thumbnailLink"),
                )
            )

        # Folder grouping: directories first, then media
        children.sort(
            key=lambda c: (c.media_class != MediaClass.DIRECTORY, c.title),
        )

        return BrowseMediaSource(
            domain=DOMAIN,
            identifier=f"folder:{folder_id}" if name == "" else "",
            media_class=MediaClass.DIRECTORY,
            media_content_type="",
            title=name or "Google Drive",
            can_play=False,
            can_expand=True,
            children=children,
            children_media_class=(
                MediaClass.DIRECTORY
                if children and children[0].media_class == MediaClass.DIRECTORY
                else MediaClass.VIDEO
            ),
        )
