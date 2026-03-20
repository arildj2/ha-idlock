# ID Lock Manager for Home Assistant

A Home Assistant custom integration for managing **Datek ID Lock 150 and 202** door locks via **ZHA (Zigbee Home Automation)**.

## Features

- **Full PIN code management** — Set, clear, enable, and disable PIN codes directly on the lock hardware via the Zigbee DoorLock cluster
- **RFID tag tracking** — Detect and manage RFID tags alongside PIN codes, shown on the same user row
- **Side panel UI** — Dedicated sidebar panel with inline editing, per-row save, and progress indicators
- **Lock settings** — Configure auto-lock, away mode, relock, audio volume, RFID, master PIN, and service PIN mode directly from the panel
- **Event-driven architecture** — Near-zero battery impact; lock pushes events to HA, no polling required
- **Real-time code change detection** — Automatically syncs when codes are added, changed, or deleted via the lock's keypad (`programming_event_notification`)
- **Lock/unlock event tracking** — Sensors and HA events for keypad, RFID, manual, and RF lock operations with code slot identification
- **Automation-ready** — Fires `ha_idlock_lock_event` and `ha_idlock_code_changed` events for HA automations

## Supported Devices

| Device | Model | Zigbee Module | Status |
|--------|-------|---------------|--------|
| ID Lock 150 | Datek ID Lock 150 | Zigbee 3.0 (certified) | Supported |
| ID Lock 202 Multi | Datek ID Lock 202 | Zigbee 3.0 (certified) | Supported |

Both models expose identical Zigbee capabilities. The only difference is that the 150 supports hinge mode configuration (left/right door), while the 202 Multi does not use this attribute.

### Device Capabilities (from official Zigbee specifications)

| Capability | Value |
|------------|-------|
| PIN code slots | 25 |
| RFID tag slots | 25 |
| PIN length | 4–10 digits |
| DoorLock commands | Lock, Unlock, Set PIN, Get PIN, Clear PIN, Clear all PINs |
| Event notifications | Operation events + Programming events (all masks enabled) |
| Power source | Battery (Zigbee EndDevice) |
| Zigbee profile | HA 0x0104, device type DOOR_LOCK (0x000A) |
| Endpoint | 1 |
| Cluster | Door Lock (0x0101) |

### IDLock Manufacturer-Specific Attributes (cluster 0x0101)

| Attribute | ID | Type | Description |
|-----------|----|------|-------------|
| Master PIN mode | 0x4000 | Boolean | True: master PIN can unlock. False: master PIN disabled for unlock |
| RFID enabled | 0x4001 | Boolean | Enable/disable RFID tag unlocking |
| Hinge mode | 0x4002 | Boolean | Door hinge side (150 only; not used on 202) |
| Service PIN mode | 0x4003 | Uint8 | 0: deactivated, 1–4: limited uses (1x/2x/5x/10x), 5: random 1-use, 6: random 24h, 7: always valid, 8: 12 hours, 9: 24 hours |
| Lock mode | 0x4004 | Uint8 | Bit 0: auto-lock (0=OFF, 1=ON), Bit 1: away mode (0=OFF, 1=ON). Away mode requires both PIN + RFID to unlock |
| Relock enabled | 0x4005 | Boolean | Auto-relock after unlock |
| Audio volume | 0x4006 | Uint8 | 0: silent, 1–5: volume levels |

> Note: Service PIN modes 5 and 6 (random PIN) require lock firmware ≥ 1.5.5. To see the generated random PIN, enter **[Master PIN] + [*] + [8]** on the lock keypad.

> Note: Manufacturer-specific attributes are written using zigpy's `_write_attributes` with manufacturer code 4919 (Datek), following the same pattern as [zha-toolkit](https://github.com/mdeweerd/zha-toolkit).

## Architecture

### Direct Zigpy Cluster Access

This integration communicates with the lock **directly via the zigpy DoorLock cluster**, not through ZHA HA services. This enables:

- **PIN read-back** — Read existing codes from the lock (ZHA services don't expose this)
- **RFID detection** — Read which slots have RFID tags enrolled
- **Lock settings** — Read and write manufacturer-specific attributes (0x4000–0x4006)
- **Full verification** — Confirm set/clear operations succeeded by reading back from hardware
- **No admin token required** — Runs inside HA with direct Python access to zigpy objects

Access path:
```
hass.data["zha"].gateway_proxy.gateway.application_controller.get_device(ieee)
  → zigpy Device → endpoints[1].in_clusters[0x0101] → DoorLock cluster
```

### Slot Numbering

The IDLock uses **1-based** slot numbering natively — both in Zigbee commands and event notifications. The UI slot numbers match the lock's physical slot numbers directly with no offset translation needed.

Each slot (1–25) represents a **user** who can have:
- A PIN code (via `set_pin_code`)
- An RFID tag (enrolled on the lock keypad)
- Both
- Neither

PIN and RFID share the same user slot, so slot 3 keypad unlock and slot 3 RFID unlock both report the same user.

### Battery-Friendly Design

The ID Lock 150/202 are battery-powered Zigbee EndDevices that sleep to conserve power. This integration is designed to minimize radio communication:

| Operation | Battery cost | When it happens |
|-----------|-------------|-----------------|
| Lock/unlock detection | **Zero** | Lock pushes `operation_event_notification` |
| Code change detection | **Zero** | Lock pushes `programming_event_notification` |
| Sensor updates | **Zero** | Driven by push events |
| Set/Clear/Enable/Disable PIN | **1 command** | User-initiated from panel |
| Change lock setting | **1 command** | User-initiated from panel |
| "Sync from lock" full read | **50 commands** | Manual button (reads 25 PIN + 25 RFID slots) |
| HA startup | **Zero** | Device lookup is local; attribute reads run in background |

After initial setup, the integration stays in sync via push notifications from the lock — **no polling, no periodic reads**.

### How Code Sync Works

1. **On first setup**: The integration reads all 25 PIN + 25 RFID slots in the background to discover existing codes
2. **When you set/clear via the panel**: Single command to lock, then store updates locally
3. **When someone adds/changes/deletes a code via the lock keypad**: The lock sends a `programming_event_notification` → integration updates its store and fires an event — **zero battery cost**
4. **"Sync from lock" button**: Manual full re-read for when you need to reconcile (e.g., after enrolling an RFID tag on the lock)
5. **No background polling** — Unlike Z-Wave integrations that can poll for free (cached data), Zigbee has no cache layer, so we never poll

### Non-Blocking Startup

The integration starts instantly without waiting for Zigbee devices. Device connections and attribute reads run in a background task after HA is fully loaded, so the integration never blocks HA startup — even if locks are asleep.

## Installation

### HACS (recommended)

1. Add this repository as a custom repository in HACS
2. Install "ID Lock Manager"
3. Restart Home Assistant

### Manual

1. Copy the `custom_components/ha_idlock/` folder to your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant

## Configuration

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **"ID Lock Manager"**
3. Select your ZHA lock entities (multi-select supported)
4. The integration will connect to each lock and discover existing codes in the background

### Requirements

- Home Assistant with **ZHA** integration configured
- ID Lock 150 or 202 paired to your ZHA coordinator
- Lock firmware ≥ 1.5.0 (for Zigbee module compatibility)

## Usage

### Side Panel

After setup, an **"ID Lock"** entry appears in the HA sidebar. The panel provides:

**Code management:**
- **User table** — Shows only occupied slots with both PIN and RFID status per row
- **Inline PIN entry** — Type a new PIN directly in the table row (digits only, 4–10 characters)
- **Inline name editing** — Type a name for each user directly in the table
- **Per-row Save** — Changes are staged locally; nothing is sent to the lock until you click Save
- **Progress indicator** — Spinning animation and row dimming while communicating with the lock
- **Enable/Disable** — Toggle individual code slots without deleting the code
- **Clear** — Remove a PIN or RFID from a slot
- **Add code** — Inline form with auto-suggested next free slot number
- **Sync from lock** — Full hardware read to discover codes/RFIDs set outside the integration

**Lock settings** (collapsible):
- **Audio volume** — Silent / Low / High
- **Master PIN can unlock** — Toggle
- **RFID enabled** — Toggle
- **Require PIN for RF** — Toggle
- **Auto-lock** — Toggle
- **Away mode** — Toggle (requires both PIN and RFID to unlock)
- **Auto-relock** — Toggle
- **Service PIN mode** — Dropdown with 10 modes (includes random PIN with instructions)
- **Device info** — PIN/RFID slot count, PIN length range, IEEE address
- **Staged saves** — All setting changes are staged; nothing sent until you click "Save settings"

### Sensors

For each managed lock, two sensor entities are created:

- **`sensor.<name>_last_event`** — Last lock/unlock event with source (keypad/RF/manual/RFID), operation, and code slot number
- **`sensor.<name>_code_change`** — Last code programming event (pin_added/pin_deleted/pin_changed/rfid_added/rfid_deleted) with source and slot number

### Events for Automations

#### `ha_idlock_lock_event`

Fired on every lock/unlock operation.

```yaml
event_data:
  lock_name: "Front Door"
  entity_id: "lock.front_door"
  device_ieee: "68:0a:e2:ff:fe:xx:xx:xx"
  source: "keypad"       # keypad, rf, manual, rfid
  operation: "unlock"    # lock, unlock, toggle, auto_lock
  code_slot: 3           # 1-based slot number, 0 for non-keypad
```

#### `ha_idlock_code_changed`

Fired when codes are added, changed, or deleted (including via the lock's physical keypad).

```yaml
event_data:
  lock_name: "Front Door"
  entity_id: "lock.front_door"
  device_ieee: "68:0a:e2:ff:fe:xx:xx:xx"
  event: "pin_added"     # pin_added, pin_deleted, pin_changed, rfid_added, rfid_deleted, master_code_changed
  source: "keypad"       # keypad, rf, manual, rfid
  code_slot: 5           # 1-based slot number
```

### Automation Examples

**Notify when someone unlocks with a specific code slot:**

```yaml
automation:
  - alias: "Notify: Kids arrived home"
    trigger:
      - platform: event
        event_type: ha_idlock_lock_event
        event_data:
          operation: unlock
          source: keypad
          code_slot: 3
    action:
      - service: notify.mobile_app
        data:
          message: "Kids arrived home (slot 3)"
```

**Alert when a code is changed via the lock keypad:**

```yaml
automation:
  - alias: "Alert: Lock code changed"
    trigger:
      - platform: event
        event_type: ha_idlock_code_changed
    action:
      - service: notify.mobile_app
        data:
          message: >
            {{ trigger.event.data.lock_name }}:
            {{ trigger.event.data.event }} on slot {{ trigger.event.data.code_slot }}
            (via {{ trigger.event.data.source }})
```

## WebSocket API

The integration exposes a WebSocket API under the `idlock/` namespace for the side panel and advanced use:

| Command | Description |
|---------|-------------|
| `idlock/list_locks` | List all managed locks |
| `idlock/get_lock` | Get single lock details |
| `idlock/set_code` | Set a PIN code on a slot |
| `idlock/clear_code` | Clear a PIN from a slot |
| `idlock/enable_code` | Enable a code slot |
| `idlock/disable_code` | Disable a code slot |
| `idlock/rename_code` | Update a slot's label |
| `idlock/clear_rfid` | Clear an RFID tag from a slot |
| `idlock/save_lock_meta` | Update lock name/max slots |
| `idlock/read_all_codes` | Full hardware read of all PIN + RFID slots |
| `idlock/get_device_settings` | Read lock settings (manufacturer attributes) |
| `idlock/set_device_setting` | Write a single lock setting |

## Technical Details

### Zigbee Communication

All lock operations use the standard ZCL DoorLock cluster (0x0101) commands:

| Operation | ZCL Command | Code |
|-----------|------------|------|
| Set PIN | `set_pin_code` | 0x05 |
| Get PIN | `get_pin_code` | 0x06 |
| Clear PIN | `clear_pin_code` | 0x07 |
| Clear all | `clear_all_pin_codes` | 0x08 |
| Get RFID | `get_rfid_code` | 0x17 |
| Clear RFID | `clear_rfid_code` | 0x18 |
| Lock | `lock_door` | 0x00 |
| Unlock | `unlock_door` | 0x01 |

Events received from the lock:

| Event | ZCL Command | Code |
|-------|------------|------|
| Lock/unlock activity | `operation_event_notification` | 0x20 |
| Code add/delete/change | `programming_event_notification` | 0x21 |

### Comparison with Other Integrations

| Feature | ha_idlock | zha_lock_manager | keymaster |
|---------|-----------|------------------|-----------|
| Target platform | ZHA (Zigbee) | ZHA (Zigbee) | Z-Wave JS |
| PIN read-back from lock | Yes | No | Yes (cached) |
| RFID tracking | Yes | No | No |
| Code change detection | Yes (push events) | No | Yes (polling) |
| Background polling | None needed | None | Every 60s (free on Z-Wave) |
| Battery impact | Minimal | Minimal | N/A (Z-Wave) |
| Lock settings UI | Yes (7 settings) | No | No |
| Manufacturer attributes | Yes (0x4000–0x4006) | No | No |
| Side panel UI | Yes (inline editing) | Yes (prompt dialogs) | No (Lovelace) |
| Per-row save | Yes | No (immediate) | No |
| Progress indicators | Yes | No | No |
| Automation events | Yes (2 event types) | No | Yes |
| Non-blocking startup | Yes | Yes | No |
| HACS installable | Yes | Yes | Yes |

## License

MIT

## Credits

Built with insights from:
- [keymaster](https://github.com/FutureTense/keymaster) — Z-Wave lock manager (provider architecture reference)
- [zha_lock_manager](https://github.com/dmoralesdev/zha_lock_manager/) — ZHA lock manager (panel UI reference)
- [zha-toolkit](https://github.com/mdeweerd/zha-toolkit) — Manufacturer attribute write pattern
- [Datek IDLock Zigbee specifications](https://idlock.no/zigbee) — Official technical documentation
