# Changelog

## 0.3.1 — 2026-10-02

- HA 2026.8+ split each lock into two devices (ZHA's and a duplicate owned by
  ID Lock Manager holding its sensors). Sensors now link to the ZHA lock device
  via `device_entry`, and the emptied duplicates are removed on setup.
- Replace the deprecated `device_registry.async_get_device` lookup (removed in
  HA 2027.8) with `async_get_devices`, falling back on older HA.

## 0.3.0 — 2026-10-02

- Fix lock events on current ZHA reporting slot N+1: ZHA adds 1 to the raw
  user ID, but ID Lock slots are already 1-based.
- Identify master-PIN unlocks (user ID 109, shown as code slot 110 by ZHA):
  `ha_idlock_lock_event` gains `credential` (`master_pin`/`pin`/`rfid`/`unknown`)
  and `user_id`; sensors show "Master PIN" instead of "slot 110".
- Normalize ZHA operation names (`AutoLock` → `auto_lock`,
  `UnlockFailureInvalidPINorID` → `unlock_failure_invalid_pin_or_id`).
- Last person: count manual/key unlocks, and don't let the state fallback
  overwrite a person already resolved from an earlier operation event.
- Save lock battery: background device-info reads run once per HA start
  instead of after nearly every lock event; failed reads back off 15 minutes.
- Read the lock model with the module build so a Zigbee module moved into a
  new lock body (150 → 202) refreshes its model; shown in the panel.
- Sync from lock clears labels of slots found empty and drops slots above the
  hardware limit; `set_code` without a label keeps the existing label.
- Panel: show lock state and battery, confirm before replacing an existing PIN,
  fix overlapping PIN reveals getting stuck, dismissable errors, ignore empty
  lock names, and only re-render when the selected lock's entities change.
- Faster entity lookup via device registry; tolerate a corrupt `max_slots`.

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
