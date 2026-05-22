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
- Automatic token refresh via Home Assistant's `OAuth2Session` helper
- Tiny footprint: ~200 lines of code, no extra Python dependencies

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

## Limitations

- **Single instance only.** One config entry per Home Assistant install, bound to one `google_drive` entry.
- **No chunked-upload progress.** The full file is sent in one PUT body. For 100+ MB files consider a different tool.
- **`drive.file` scope.** This integration can read/manage only files it itself created. Listing or sharing files uploaded by other apps is not supported.

## License

MIT. See [LICENSE](LICENSE).
