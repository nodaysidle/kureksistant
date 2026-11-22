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

## Palette (light, 0.2.5)
| Role | Hex | Use |
|---|---|---|
| background | `#F6F5F1` | window |
| surface | `#FFFFFF` | header, input bar, Kurek messages, dialog |
| surface-low | `#EEEDE8` | input field, tool-call rows |
| outline | `#DAD7CF` | 1dp hairlines and borders |
| text | `#1B1C1A` | primary text |
| text-muted | `#5D605A` | secondary text |
| accent | `#2E6B3A` | online dot, Send, primary action, focus ring |
| accent-container | `#DDEBDD` | own messages |
| warn / container | `#8A5A00` / `#F6E7C6` | Thinking, Busy, Connecting |
| error / container | `#B3261E` / `#F9DEDC` | Error, error messages, recording |
| offline | `#8B8E88` | Offline dot |

Light only, also when the system is in dark mode. The status word always shows next to the dot.

## Typography
- System sans for all text: 20sp medium title, 16sp body on a 22sp line, 14sp buttons and labels, 12sp meta.
- JetBrains Mono only for what is technical: the host, tool names and timestamps.
- Sentence case, no all-caps. No emoji anywhere; icons are vector drawables.

## Layout
- Top bar: `Kurek` title + connection status (dot + word + host) on the left; actions on the right:
  speaker toggle, settings.
- Preset row under the top bar, outlined pill chips: **Terminal** (herdr on the PC), **Open GrokBot**,
  **System activity**, **Projects**, **Screen**, **Talk live**.
- Conversation fills the screen. Tool calls stay visible inline as monospace lines, like log output.
- Input bar at the bottom: text field, send, mic. Respects keyboard (IME) and gesture-nav insets.
- Edge-to-edge with correct system-bar insets; tap targets at least 48dp.

## Motion
Hover/press feedback and state color changes only. No fades, bounces, or glows.
