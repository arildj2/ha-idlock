# Changelog

## 0.2.0 — 2026-08-06

- Require successful ZCL status responses and matching hardware read-back for
  PIN and RFID mutations before updating local state.
- Serialize Zigbee traffic per lock to prevent overlapping panel/client work.
- Use one overall device-info timeout, public zigpy raw-attribute APIs, a
  five-minute settings cache, and an explicit settings refresh.
- Derive slot and PIN limits from device capabilities and remove editable slot
  limits from the panel API.
- Track background work with the config entry, flush delayed storage writes on
  unload, and clean up partial setup failures.
- Fix last-person attribution when the Home Assistant state event arrives
  before the detailed ZHA operation event.
- Remove production debug endpoints that exposed raw PIN/device data.
- Mask and auto-hide PINs, invalidate stale frontend requests, add accessible
  confirmations and controls, and improve mobile table handling.
- Add Python/frontend tests, Ruff, CI, hassfest/HACS validation, licensing, and
  release metadata.

## 0.1.1

- Harden panel loading and device/event handling.
- Vendor Lit locally so the custom panel has no CDN runtime dependency.
