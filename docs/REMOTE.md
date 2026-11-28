# Phone remote (Kurek Remote)

Kurek Remote is an Android app that sends prompts to the daemon. Remote access is off by default. Without a token and a bind mode, the daemon listens on `127.0.0.1:8790` only.

## Pairing over Tailscale

1. In `.env`, set a token of 32 characters or more. Generate one with `openssl rand -hex 32`.

   ```bash
   KUREK_REMOTE_TOKEN=<the 64-character hex string>
   KUREK_REMOTE_BIND=tailscale
   ```

2. Restart the daemon with `systemctl --user restart kurek.service`.
3. Install the Kurek Remote debug APK from the release page, or build it from [`android/`](../android/) with `./gradlew :app:assembleDebug`.
4. In the app settings, enter the PC's Tailscale IPv4 address or MagicDNS name, port `8790`, and the same token.

If Tailscale has no IPv4 address yet, the daemon listens on loopback and retries when Tailscale comes up.

## Settings

| Variable | Default | Effect |
|---|---|---|
| `KUREK_REMOTE_TOKEN` | empty | Required for any remote bind. 32 characters or more. |
| `KUREK_REMOTE_BIND` | empty | `tailscale` binds to the Tailscale address. An explicit host address is also accepted. |
| `KUREK_BIND` | empty | Explicit bind host, for example `127.0.0.1`. `0.0.0.0` exposes every interface, so only use it if you understand the risk. |
| `KUREK_REMOTE_ALLOW_EXEC` | off | Lets remote prompts run `desktop_control`, `code_helper`, `dev_agent`, `run` and `codebase`. It also enables the unrestricted shell in `run`, locally and remotely. |
| `KUREK_REMOTE_LOCAL_TTS` | off | Set to `1` so remote prompts also speak on the PC. By default they are silent on the PC. |
| `KUREK_LIVE_VOICE` | off | Set to `1` to enable real-time voice calls from the phone. |
| `KUREK_LIVE_PORT` | `8791` | Port for the live voice socket at `ws://<host>:<port>/live`. |

## Live voice

Live voice is talk-only, uses xAI speech-to-speech, and is billed per minute by xAI. It needs `KUREK_REMOTE_TOKEN` and exposes only the read-only tools.

## Alerts

Camera alerts are pushed to the app over the same connection. See [CAMERA.md](CAMERA.md).
