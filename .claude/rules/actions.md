---
paths:
  - "actions/**"
---

# Action Tool Modules (`actions/`) Rules

1. **Subprocess & Desktop Safety**:
   - Every external subprocess call (e.g. `hyprctl`, shell commands) must specify an explicit `timeout` to prevent daemon hangs.
   - Hyprland window actions must query state before toggling (e.g. fullscreen toggle reads current state first).

2. **Terminal Preference**:
   - When launching terminal applications, use the default terminal (Ghostty); run `herdr` inside it.

3. **Graceful Fallbacks**:
   - If a desktop tool, window, or app is missing or fails to launch, catch exceptions and return a concise error message rather than crashing the daemon engine.
