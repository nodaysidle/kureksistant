# Kurek Remote — Design Direction

Source: the owner's own words (2026-10-27) plus their active Omarchy theme, **Nodaysidle Miasma**
(`~/.local/state/omarchy/current/theme/colors.toml`). The app should feel like the phone end of
their desktop, not a separate product.

## Identity
- A calm, minimal **terminal companion** for an Omarchy / Hyprland desktop.
- Quiet by default: one focal point per screen (the conversation), everything else defers.
- **No emoji anywhere** in the UI (owner's rule). Use plain text labels and simple line icons only
  where an icon is clearer than a word (send, mic, settings, speaker).

Dial: ENERGY 1 / RHYTHM 1 / MOTION 1

## Palette (Miasma)
| Role | Hex | Use |
|---|---|---|
| background | `#141414` | window |
| surface | `#1e1e1e` | input bar, Kurek messages, sheets |
| surface-low | `#0e0e0e` | status bar / nav bar area, tool-call lines |
| outline | `#363832` | 1dp dividers and borders |
| text | `#e6e4d9` | primary text |
| text-muted | `#8a8a7d` | secondary text, timestamps |
| accent | `#87b070` | the one accent: online dot, primary action, focus ring, own messages edge |
| warn (thinking) | `#e4c47a` | THINKING / CONNECTING state only |
| error | `#e06c75` | errors and OFFLINE only |

One accent (moss green). Gold and red are status colors, never decoration.

## Typography
- **JetBrains Mono** (the owner's Foot font) for app chrome: top bar title, status, preset buttons,
  tool-call lines, timestamps.
- System sans (Roboto via `sans-serif`) for chat message bodies, for readability of long text.
- Sizes: 12sp meta, 14sp labels, 15sp body, 18sp title. No all-caps with wide tracking.

## Layout
- Top bar: `kurek` title + connection status (dot + word) on the left; actions on the right:
  **Terminal** (opens Foot with herdr on the PC), speaker toggle, settings.
- Preset row under the top bar, plain outlined buttons, monospace: **Open GrokBot**,
  **System activity**, **Projects**, **Screen**, **Talk live**.
- Conversation fills the screen. Tool calls stay visible inline as monospace lines, like log output.
- Input bar at the bottom: text field, send, mic. Respects keyboard (IME) and gesture-nav insets.
- Edge-to-edge with correct system-bar insets; tap targets at least 48dp.

## Motion
Hover/press feedback and state color changes only. No fades, bounces, or glows.
