"""HTTP view that proxies Drive file content through Home Assistant.

Why a proxy? Drive's `uc?export=download` URL — the obvious choice — always
sends `Content-Disposition: attachment`, which makes browsers download instead
of play the file inline in a `<video>` tag. The Files API `?alt=media`
endpoint serves bytes correctly but requires a Bearer token the browser
cannot present.

This view sits between: it authenticates the browser via the normal HA
session (cookie), fetches from Drive server-side using our OAuth2Session,
and streams the bytes back with `Content-Disposition: inline`. Result: any
HA-authenticated user can play any Drive file we have uploaded, even if the
file does not have anyone-with-link permission.

HTTP Range requests are forwarded to Drive so HTML5 `<video>` seeking works.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from aiohttp import ClientError, web

from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .api import DriveApi
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DRIVE_FILE_GET_URL = "https://www.googleapis.com/drive/v3/files/{file_id}?alt=media"
CHUNK_SIZE = 65536

# Headers we forward Drive → browser so HTML5 video seeking works.
_PASSTHROUGH_RESPONSE_HEADERS = ("Content-Range", "Content-Type", "Last-Modified")


class GdriveStreamView(HomeAssistantView):
    """Proxy a single Drive file (by id) through HA's authenticated HTTP layer."""

    url = "/api/gdrive_upload/stream/{file_id}"
    name = "api:gdrive_upload:stream"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    def _get_api(self) -> DriveApi | None:
        data = self.hass.data.get(DOMAIN)
        if not data:
            return None
        return next(iter(data.values()))

    async def get(self, request: web.Request, file_id: str) -> web.StreamResponse:
        api = self._get_api()
        if api is None:
            return web.Response(status=503, text="gdrive_upload not configured")

        drive_url = DRIVE_FILE_GET_URL.format(file_id=file_id)

        # Forward the browser's Range header to Drive so seeking works.
        fetch_kwargs: dict[str, Any] = {}
        if (range_header := request.headers.get("Range")) is not None:
            fetch_kwargs["headers"] = {"Range": range_header}

        try:
            drive_resp = await api._request("GET", drive_url, **fetch_kwargs)
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Drive proxy fetch failed for %s: %s", file_id, err)
            return web.Response(status=502, text=f"drive fetch failed: {err}")

        try:
            if drive_resp.status not in (200, 206):
                body = await drive_resp.text()
                _LOGGER.info(
                    "Drive returned %s for %s: %s",
                    drive_resp.status,
                    file_id,
                    body[:200],
                )
                return web.Response(status=drive_resp.status, text=body[:500])

            stream_headers: dict[str, str] = {
                "Content-Disposition": "inline",
                "Accept-Ranges": "bytes",
                "Cache-Control": "private, max-age=300",
            }
            for header in _PASSTHROUGH_RESPONSE_HEADERS:
                if (value := drive_resp.headers.get(header)) is not None:
                    stream_headers[header] = value
            stream_headers.setdefault("Content-Type", "application/octet-stream")

            stream = web.StreamResponse(status=drive_resp.status, headers=stream_headers)
            if (cl := drive_resp.headers.get("Content-Length")) is not None:
                with contextlib.suppress(ValueError):
                    stream.content_length = int(cl)
            await stream.prepare(request)

            try:
                async for chunk in drive_resp.content.iter_chunked(CHUNK_SIZE):
                    await stream.write(chunk)
                await stream.write_eof()
            except (asyncio.CancelledError, ConnectionError, ClientError) as err:
                # Browser hung up or upstream connection broke. Don't try to
                # finalise the stream — aiohttp will close the half-sent
                # response cleanly when this handler returns.
                _LOGGER.debug("Stream interrupted for %s: %s", file_id, err)
            return stream
        finally:
            drive_resp.release()
