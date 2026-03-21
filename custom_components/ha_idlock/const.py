"""Constants for the ID Lock integration."""

from homeassistant.const import Platform

DOMAIN = "ha_idlock"

STORAGE_KEY = DOMAIN
STORAGE_VERSION = 1

CONF_LOCKS = "locks"

EVENT_IDLOCK = "ha_idlock_lock_event"
EVENT_IDLOCK_CODE_CHANGED = "ha_idlock_code_changed"
EVENT_ZHA = "zha_event"

# Zigbee
BASIC_CLUSTER_ID = 0x0000
DOOR_LOCK_CLUSTER_ID = 0x0101
ZHA_DOMAIN = "zha"

# IDLock manufacturer code (Datek)
IDLOCK_MANUFACTURER_CODE = 4919

# Basic cluster manufacturer-specific attributes
ATTR_LOCK_FW_VERSION = 0x5000  # ASCII_STR: lock firmware version (e.g. "1.5.0")

# IDLock 202 defaults (from official Zigbee spec)
DEFAULT_NUM_PIN_SLOTS = 25
DEFAULT_NUM_RFID_SLOTS = 25
DEFAULT_MAX_PIN_LEN = 10
DEFAULT_MIN_PIN_LEN = 4

# IDLock manufacturer-specific attributes (cluster 0x0101)
ATTR_MASTER_PIN_MODE = 0x4000  # BOOLEAN: true=unlock enabled, false=disabled
ATTR_RFID_ENABLED = 0x4001  # BOOLEAN: true/false
ATTR_HINGE_MODE = 0x4002  # BOOLEAN: not used
ATTR_SERVICE_PIN_MODE = 0x4003  # UINT8: 0=off, 1-4=limited uses, 5-6=random, 7=always, 8=12h, 9=24h
ATTR_LOCK_MODE = 0x4004  # UINT8: 0-3 auto-lock/away-mode combinations
ATTR_RELOCK_ENABLED = 0x4005  # BOOLEAN: true/false
ATTR_AUDIO_VOLUME = 0x4006  # UINT8: 0=silent, 1-5=volume levels

# Service PIN mode values
SERVICE_PIN_DEACTIVATED = 0
SERVICE_PIN_1_USE = 1
SERVICE_PIN_2_USES = 2
SERVICE_PIN_5_USES = 3
SERVICE_PIN_10_USES = 4
SERVICE_PIN_RANDOM_1_USE = 5
SERVICE_PIN_RANDOM_24H = 6
SERVICE_PIN_ALWAYS_VALID = 7
SERVICE_PIN_12_HOURS = 8
SERVICE_PIN_24_HOURS = 9

# Lock mode values
LOCK_MODE_AUTO_OFF_AWAY_OFF = 0
LOCK_MODE_AUTO_ON_AWAY_OFF = 1
LOCK_MODE_AUTO_OFF_AWAY_ON = 2
LOCK_MODE_AUTO_ON_AWAY_ON = 3

# Programming event codes (from ZCL DoorLock cluster command 0x21)
PROG_EVENT_UNKNOWN = 0
PROG_EVENT_MASTER_CODE_CHANGED = 1
PROG_EVENT_PIN_ADDED = 2
PROG_EVENT_PIN_DELETED = 3
PROG_EVENT_PIN_CHANGED = 4
PROG_EVENT_RFID_ADDED = 5
PROG_EVENT_RFID_DELETED = 6

# Platforms
PLATFORMS: list[Platform] = [Platform.SENSOR]

# Frontend / panel
PANEL_URL_BASE = "/ha-idlock-frontend"
PANEL_MODULE_URL = f"{PANEL_URL_BASE}/ha_idlock_panel.js?v=30"
PANEL_PATH = "frontend"
PANEL_TITLE = "ID Lock"
PANEL_ICON = "mdi:lock-smart"
PANEL_URL_PATH = "ha-idlock"

# WebSocket command types
WS_NS = "idlock"
WS_LIST_LOCKS = f"{WS_NS}/list_locks"
WS_GET_LOCK = f"{WS_NS}/get_lock"
WS_SET_CODE = f"{WS_NS}/set_code"
WS_CLEAR_CODE = f"{WS_NS}/clear_code"
WS_ENABLE_CODE = f"{WS_NS}/enable_code"
WS_DISABLE_CODE = f"{WS_NS}/disable_code"
WS_RENAME_CODE = f"{WS_NS}/rename_code"
WS_SAVE_LOCK_META = f"{WS_NS}/save_lock_meta"
WS_READ_ALL_CODES = f"{WS_NS}/read_all_codes"
WS_READ_PIN = f"{WS_NS}/read_pin"
