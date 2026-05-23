# Google Drive Upload — Home Assistant Custom Integration

[![hacs_custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz/docs/faq/custom_repositories)
[![License](https://img.shields.io/github/license/snordquist/hass-gdrive-upload)](LICENSE)

Service to upload arbitrary local files to Google Drive from a Home Assistant automation or script. Reuses the OAuth token of Home Assistant's built-in `google_drive` integration — no separate Google Cloud Console setup, no `credentials.json`.

## Why this exists

Home Assistant's built-in `google_drive` integration is backup-only: it exposes no `upload_file` service. Existing community alternatives require setting up a separate OAuth client (Google Cloud Console → JSON credentials → manual file upload). This integration sits on top of the existing `google_drive` config entry's OAuth session, so once you have Drive backups working, file uploads work too — no extra credentials.

## Features

- Single service: `gdrive_upload.upload`
- Creates Drive folder paths recursively if missing (e.g. `Photos/2026/05/`)
- Returns Drive file ID, web view link, and (optionally) an anyone-with-link share URL — all synchronously in the service response, so the calling automation can chain notifications
- Exposes uploads as a **Home Assistant Media Source** (`media-source://gdrive_upload/`), so the standard Media Browser, Camera Gallery Card, and any other media-source-aware component can browse and play files directly from Drive
- Automatic token refresh via Home Assistant's `OAuth2Session` helper
- Tiny footprint: ~250 lines of code, no extra Python dependencies

## Requirements

- Home Assistant 2024.1 or newer
- The built-in `google_drive` integration installed and authorized (used as the OAuth source)

## Installation

### HACS (recommended)

1. HACS → Integrations → ⋮ → **Custom Repositories**
2. URL: `https://github.com/snordquist/hass-gdrive-upload`, Category: **Integration**
3. Install **Google Drive Upload**, restart Home Assistant
4. Settings → Devices & Services → **Add Integration** → search "Google Drive Upload"

If exactly one `google_drive` config entry exists, the config flow completes silently with no form. If multiple Drive accounts are configured, a dropdown lets you pick which one to use.

### Manual

```bash
cd /config
git clone https://github.com/snordquist/hass-gdrive-upload.git /tmp/hass-gdrive-upload
cp -r /tmp/hass-gdrive-upload/custom_components/gdrive_upload custom_components/
# Restart Home Assistant
```

## Usage

The integration exposes one service:

### `gdrive_upload.upload`

| Field | Type | Required | Description |
|---|---|---|---|
| `file_path` | string | yes | Absolute local path of the file to upload (must be readable by Home Assistant) |
| `folder_path` | string | yes | Slash-delimited Drive folder path. Created recursively if missing |
| `share` | boolean | no | Default `false`. If `true`, an anyone-with-link reader permission is added and `share_url` is returned |

Response (synchronous, when called with `return_response: true`):

```yaml
file_id: 1AbCdEfGhIjKlMnOpQrStUv
web_view_link: https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view?usp=drivesdk
share_url: https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUv/view   # only when share=true
```

### Example

```yaml
script:
  archive_recording_to_drive:
    sequence:
      - service: gdrive_upload.upload
        data:
          file_path: /media/cam1/2026-05-22_19-04-32.mp4
          folder_path: "Recordings/{{ now().strftime('%Y/%m') }}"
          share: true
        response_variable: result
      - service: notify.mobile_app_iphone
        data:
          title: Recording archived
          message: "Open on Drive"
          data:
            url: "{{ result.share_url }}"
```

## How it works

```
  ┌───────────────────────────────────────────────────────────┐
  │  Home Assistant                                            │
  │                                                            │
  │  ┌──────────────────┐         ┌────────────────────────┐ │
  │  │  google_drive    │  OAuth  │  Google OAuth servers  │ │
  │  │  config entry    │ ◄────── │  (token refresh)       │ │
  │  └────────┬─────────┘         └────────────────────────┘ │
  │           │ borrowed OAuth2Session                        │
  │           ▼                                                │
  │  ┌──────────────────┐  REST   ┌────────────────────────┐ │
  │  │  gdrive_upload   │ ──────► │  Google Drive API v3   │ │
  │  │  upload service  │         │  (files, permissions)  │ │
  │  └──────────────────┘         └────────────────────────┘ │
  └───────────────────────────────────────────────────────────┘
```

The integration retrieves the OAuth client implementation from the `google_drive` config entry, constructs an `OAuth2Session` bound to that entry, and uses it to call the Drive API. Token refresh and 401 retries are handled by Home Assistant's OAuth helper.

The Drive `drive.file` scope (which `google_drive` requests) permits the app to create files and manage files it created — including sharing — which covers everything this integration does.

## Browsing uploads as a Media Source

Once installed and configured, the integration registers a media source called **Google Drive Upload** that lands in the Drive folder `HomeAssistant/` (the parent the upload service uses by default). Subfolders are expanded on demand; videos and images are playable inline via the Drive `?export=download` URL — provided the file has anyone-with-link permission (the upload service sets this when called with `share: true`).

Media-source URI: `media-source://gdrive_upload/`

The integration registers `ROOT_FOLDER_PATH = "HomeAssistant/Doorbell"` as the browse entry point. Subfolders below that (e.g. `2026/05/`) are expanded on demand. Anything you upload via the service into a different folder is invisible to the browser but still reachable by file id.

In a Lovelace card that accepts a media source list — e.g. [Camera Gallery Card](https://github.com/TheScubaDiver/camera-gallery-card):

```yaml
type: custom:camera-gallery-card
source_mode: media
media_sources:
  - media-source://media_source/local/doorbell    # local cache
  - media-source://gdrive_upload/                  # Drive archive
path_datetime_format: YYYY-MM-DD_HH-mm-ss
```

### Playback

Files are streamed through an internal Home Assistant HTTP view (`/api/gdrive_upload/stream/<file_id>`). The browser uses its existing HA session cookie; the server uses the gdrive_upload OAuth session to fetch the bytes from Drive. This means:

- Inline `<video>` playback works regardless of the file's share state — anyone-with-link permission is not required.
- A user must be signed in to Home Assistant to play a file. The stream URL leaks neither a Drive id nor a token if intercepted.

The Drive `drive.file` OAuth scope means this integration can only see files the integration itself uploaded — anything you added to Drive via the web UI or another app is invisible to it.

## Limitations

- **Single instance only.** One config entry per Home Assistant install, bound to one `google_drive` entry.
- **No chunked-upload progress.** The full file is sent in one PUT body. For 100+ MB files consider a different tool.
- **`drive.file` scope.** This integration can read/manage only files it itself created. Listing or sharing files uploaded by other apps is not supported.

## License

MIT. See [LICENSE](LICENSE).
