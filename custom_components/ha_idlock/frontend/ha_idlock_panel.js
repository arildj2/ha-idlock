import {
  LitElement,
  html,
  css,
} from "https://unpkg.com/lit-element@4.1.1/lit-element.js?module";

class HaIdlockPanel extends LitElement {
  static get properties() {
    return {
      hass: { type: Object },
      narrow: { type: Boolean },
      route: { type: Object },
      panel: { type: Object },
      _locks: { type: Array },
      _selected: { type: Object },
      _busy: { type: Boolean },
      _busySlot: { type: Number },
      _busyAction: { type: String },
      _error: { type: String },
      _settings: { type: Object },
      _pendingSettings: { type: Object },
      _dirty: { type: Object },
      _revealedPins: { type: Object },
    };
  }

  constructor() {
    super();
    this._locks = [];
    this._selected = null;
    this._settings = null;
    this._busy = false;
    this._busySlot = 0;
    this._busyAction = "";
    this._error = "";
    this._dirty = {};  // { slotNum: { label: "...", pin: "..." } }
    this._pendingSettings = {};  // { setting_name: value } — unsaved setting changes
    this._revealedPins = {};  // { slotNum: "1234" | "loading" }
  }

  connectedCallback() {
    super.connectedCallback();
    this._refresh();
    // Refresh when returning from idle/sleep/tab switch
    this._visibilityHandler = () => {
      if (document.visibilityState === "visible") {
        // Delay to let HA's WebSocket reconnect after sleep/idle
        setTimeout(() => {
          if (this.hass) {
            this.requestUpdate();
            this._refresh();
          }
        }, 500);
      }
    };
    document.addEventListener("visibilitychange", this._visibilityHandler);
  }

  disconnectedCallback() {
    super.disconnectedCallback();
    if (this._visibilityHandler) {
      document.removeEventListener("visibilitychange", this._visibilityHandler);
    }
  }

  updated(changedProperties) {
    if (changedProperties.has("hass") && this.hass) {
      // hass gets re-assigned on reconnect — always refresh if we have no data
      if (this._locks.length === 0) {
        this._refresh();
      }
      // Force repaint in case shadow DOM went stale
      this.requestUpdate();
    }
  }

  get isMobile() {
    return this.narrow || window.innerWidth <= 900;
  }

  async _ws(type, payload = {}) {
    return this.hass.callWS({ type, ...payload });
  }

  async _refresh() {
    try {
      this._error = "";
      this._locks = await this._ws("idlock/list_locks");
      if (this._selected) {
        const updated = this._locks.find(
          (l) => l.device_ieee === this._selected.device_ieee
        );
        if (updated) this._selected = updated;
      } else if (this._locks.length > 0) {
        this._selected = this._locks[0];
      }
      // Always load settings for the selected lock if not yet loaded
      if (this._selected && !this._settings) {
        this._loadSettings(this._selected.device_ieee);
      }
    } catch (e) {
      this._error = e.message || "Failed to load locks";
    }
  }

  async _readFromLock() {
    if (!this._selected) return;
    this._busy = true;
    this._busyAction = "Syncing from lock...";
    try {
      const result = await this._ws("idlock/read_all_codes", {
        device_ieee: this._selected.device_ieee,
      });
      this._selected = result;
      await this._refresh();
      // Reload settings (only hits Zigbee if not already loaded)
      await this._loadSettings(this._selected.device_ieee);
    } catch (e) {
      this._error = e.message || "Failed to read from lock";
    }
    this._busy = false;
    this._busyAction = "";
  }

  async _setCode(slotNum, code, label) {
    if (!code || !/^\d{4,10}$/.test(code)) {
      this._error = "PIN must be 4-10 digits";
      return;
    }
    this._busy = true;
    this._busySlot = slotNum;
    this._busyAction = "Setting PIN...";
    this._error = "";
    try {
      await this._ws("idlock/set_code", {
        device_ieee: this._selected.device_ieee,
        slot: slotNum,
        code,
        label: label || "",
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to set code";
    }
    this._busy = false;
    this._busySlot = 0;
    this._busyAction = "";
  }

  _handlePinInput(slot, e) {
    const code = e.target.value.trim();
    const originalPin = this._revealedPins[slot.slot];
    // Only mark dirty if the PIN differs from the revealed (original) value
    if (code && code !== originalPin) {
      this._markDirty(slot.slot, "pin", code);
    } else {
      const d = this._dirty[slot.slot];
      if (d) {
        delete d.pin;
        if (!Object.keys(d).length) delete this._dirty[slot.slot];
        this._dirty = { ...this._dirty };
      }
    }
  }

  _handlePinKeydown(slot, e) {
    if (e.key === "Enter") {
      e.preventDefault();
      this._commitRow(slot);
      return;
    }
    // Allow: backspace, delete, tab, escape, arrows, home, end
    if (["Backspace", "Delete", "Tab", "Escape", "ArrowLeft", "ArrowRight", "Home", "End"].includes(e.key)) {
      return;
    }
    // Allow Ctrl/Cmd + A, C, V, X
    if ((e.ctrlKey || e.metaKey) && ["a", "c", "v", "x"].includes(e.key.toLowerCase())) {
      return;
    }
    // Block anything that isn't 0-9
    if (!/^[0-9]$/.test(e.key)) {
      e.preventDefault();
    }
  }

  async _clearRfid(slotNum) {
    if (!confirm(`Remove RFID tag from slot ${slotNum}?`)) return;
    this._busy = true;
    this._busySlot = slotNum;
    this._busyAction = "Clearing RFID...";
    this._error = "";
    try {
      await this._ws("idlock/clear_rfid", {
        device_ieee: this._selected.device_ieee,
        slot: slotNum,
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to clear RFID";
    }
    this._busy = false;
    this._busySlot = 0;
    this._busyAction = "";
  }

  async _clearCode(slotNum) {
    if (!confirm(`Clear PIN code from slot ${slotNum}?`)) return;
    this._busy = true;
    this._busySlot = slotNum;
    this._busyAction = "Clearing PIN...";
    this._error = "";
    try {
      await this._ws("idlock/clear_code", {
        device_ieee: this._selected.device_ieee,
        slot: slotNum,
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to clear code";
    }
    this._busy = false;
    this._busySlot = 0;
    this._busyAction = "";
  }

  async _toggleCode(slot) {
    const cmd = slot.enabled ? "idlock/disable_code" : "idlock/enable_code";
    const action = slot.enabled ? "Disabling..." : "Enabling...";
    this._busy = true;
    this._busySlot = slot.slot;
    this._busyAction = action;
    this._error = "";
    try {
      await this._ws(cmd, {
        device_ieee: this._selected.device_ieee,
        slot: slot.slot,
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to toggle code";
    }
    this._busy = false;
    this._busySlot = 0;
    this._busyAction = "";
  }

  async _commitRow(slot) {
    const d = this._dirty[slot.slot];
    if (!d) return;

    const newPin = d.pin;
    const newLabel = d.label;

    // Validate PIN if changed
    if (newPin && !/^\d{4,10}$/.test(newPin)) {
      this._error = "PIN must be 4-10 digits";
      return;
    }

    // Set PIN if changed
    if (newPin) {
      await this._setCode(slot.slot, newPin, newLabel ?? slot.label ?? "");
    }
    // Rename if label changed (and no PIN change, since _setCode already sends label)
    else if (newLabel !== undefined) {
      await this._renameSlot(slot.slot, newLabel);
    }

    // Clear dirty state and PIN input
    delete this._dirty[slot.slot];
    this._dirty = { ...this._dirty };
    const pinInput = this.shadowRoot.querySelector(`#pin-${slot.slot}`);
    if (pinInput) pinInput.value = "";
  }

  async _renameSlot(slotNum, newLabel) {
    this._busy = true;
    try {
      await this._ws("idlock/rename_code", {
        device_ieee: this._selected.device_ieee,
        slot: slotNum,
        label: newLabel,
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to rename";
    }
    this._busy = false;
  }

  _markDirty(slotNum, field, value) {
    const d = this._dirty[slotNum] || {};
    d[field] = value;
    this._dirty = { ...this._dirty, [slotNum]: d };
  }

  _isDirty(slotNum) {
    return !!this._dirty[slotNum];
  }

  _handleLabelInput(slot, e) {
    const newLabel = e.target.value;
    if (newLabel !== (slot.label || "")) {
      this._markDirty(slot.slot, "label", newLabel);
    } else {
      // Unchanged — remove dirty label
      const d = this._dirty[slot.slot];
      if (d) {
        delete d.label;
        if (!Object.keys(d).length) delete this._dirty[slot.slot];
        this._dirty = { ...this._dirty };
      }
    }
  }

  _handleLabelKeydown(slot, e) {
    if (e.key === "Enter") {
      e.preventDefault();
      this._commitRow(slot);
    }
  }

  async _revealPin(slotNum) {
    this._revealedPins = { ...this._revealedPins, [slotNum]: "loading" };
    try {
      const result = await this._ws("idlock/read_pin", {
        device_ieee: this._selected.device_ieee,
        slot: slotNum,
      });
      const code = result.code || "";
      this._revealedPins = { ...this._revealedPins, [slotNum]: code };
      // Put the PIN into the input field without marking dirty
      const pinInput = this.shadowRoot.querySelector(`#pin-${slotNum}`);
      if (pinInput) {
        pinInput.value = code;
      }
    } catch (e) {
      this._revealedPins = { ...this._revealedPins, [slotNum]: "error" };
      this._error = e.message || "Failed to read PIN";
    }
  }

  async _saveMeta(name, maxSlots) {
    if (!this._selected) return;
    this._busy = true;
    try {
      await this._ws("idlock/save_lock_meta", {
        device_ieee: this._selected.device_ieee,
        name: name ?? this._selected.name,
        max_slots: maxSlots ?? this._selected.max_slots,
      });
      await this._refresh();
    } catch (e) {
      this._error = e.message || "Failed to save";
    }
    this._busy = false;
  }

  _selectLock(lock) {
    this._selected = lock;
    this._settings = null;
    this._dirty = {};
    this._pendingSettings = {};
    this._revealedPins = {};
    this._error = "";
    this._loadSettings(lock.device_ieee);
  }

  async _loadSettings(ieee) {
    try {
      this._settings = await this._ws("idlock/get_device_settings", { device_ieee: ieee });
    } catch (e) {
      this._settings = null;
    }
  }

  _stageSetting(setting, value) {
    const original = this._settings?.[setting];
    const pending = { ...this._pendingSettings };

    // Compare using loose equality to handle bool/int/null mismatches
    // e.g. checkbox gives true, original might be 1 or null
    let matches = false;
    if (original === null || original === undefined) {
      // No original value loaded — can't determine if changed, always stage it
      matches = false;
    } else if (typeof value === "boolean") {
      matches = value === !!original;
    } else if (typeof value === "number") {
      matches = value === Number(original);
    } else {
      matches = value === original;
    }

    if (matches) {
      delete pending[setting];
    } else {
      pending[setting] = value;
    }
    this._pendingSettings = pending;
  }

  _stageSettingLockModeBit(bit, enabled) {
    const current = this._pendingSettings.lock_mode ?? this._settings?.lock_mode ?? 0;
    const newMode = enabled ? (current | bit) : (current & ~bit);
    this._stageSetting("lock_mode", newMode);
  }

  get _hasSettingsChanges() {
    return Object.keys(this._pendingSettings).length > 0;
  }

  async _commitSettings() {
    if (!this._selected || !this._hasSettingsChanges) return;
    this._busy = true;
    this._busyAction = "Saving settings...";
    this._error = "";
    try {
      for (const [setting, value] of Object.entries(this._pendingSettings)) {
        this._settings = await this._ws("idlock/set_device_setting", {
          device_ieee: this._selected.device_ieee,
          setting,
          value,
        });
      }
      this._pendingSettings = {};
    } catch (e) {
      this._error = e.message || "Failed to save settings";
    }
    this._busy = false;
    this._busyAction = "";
  }

  render() {
    return html`
      <div class="container ${this.isMobile ? "mobile" : "desktop"}">
        <div class="header">
          <h1>ID Lock Manager</h1>
          <div class="header-actions">
            <button @click=${this._refresh} ?disabled=${this._busy}>
              Refresh
            </button>
          </div>
        </div>

        ${this._error
          ? html`<div class="error">${this._error}</div>`
          : ""}

        <div class="layout">
          <div class="lock-list">
            <h2>Locks</h2>
            ${this._locks.map(
              (lock) => html`
                <div
                  class="lock-item ${this._selected?.device_ieee ===
                  lock.device_ieee
                    ? "selected"
                    : ""}"
                  @click=${() => this._selectLock(lock)}
                >
                  <ha-icon icon="mdi:lock-smart"></ha-icon>
                  <span>${lock.name}</span>
                </div>
              `
            )}
            ${this._locks.length === 0
              ? html`<p class="empty">No locks configured</p>`
              : ""}
          </div>

          <div class="lock-detail">
            ${this._selected
              ? this._renderLockDetail()
              : html`<p class="empty">Select a lock</p>`}
          </div>
        </div>
      </div>
    `;
  }

  _getNextFreeSlot() {
    const lock = this._selected;
    if (!lock) return 1;
    const usedSlots = new Set(
      Object.values(lock.slots || {})
        .filter((s) => s.has_code || s.has_rfid)
        .map((s) => s.slot)
    );
    const max = lock.max_slots || 25;
    for (let i = 1; i <= max; i++) {
      if (!usedSlots.has(i)) return i;
    }
    return null;
  }

  async _submitAddCode() {
    const slotEl = this.shadowRoot.querySelector("#add-slot");
    const nameEl = this.shadowRoot.querySelector("#add-name");
    const pinEl = this.shadowRoot.querySelector("#add-pin");
    if (!slotEl || !pinEl) return;

    const slot = parseInt(slotEl.value);
    const max = this._selected?.max_slots || 25;
    if (isNaN(slot) || slot < 1 || slot > max) {
      this._error = `Slot must be 1-${max}`;
      return;
    }

    const code = pinEl.value.trim();
    if (!code || !/^\d{4,10}$/.test(code)) {
      this._error = "PIN must be 4-10 digits";
      return;
    }

    const label = nameEl?.value?.trim() || "";

    this._busyAction = "Adding PIN...";
    await this._setCode(slot, code, label);

    // Clear inputs on success
    if (!this._error) {
      pinEl.value = "";
      if (nameEl) nameEl.value = "";
      const nextFree = this._getNextFreeSlot();
      if (nextFree) slotEl.value = String(nextFree);
    }
  }

  _renderLockDetail() {
    const lock = this._selected;
    const slots = Object.values(lock.slots || {}).sort(
      (a, b) => a.slot - b.slot
    );

    // Show slots that have PIN or RFID
    const activeSlots = slots.filter((s) => s.has_code || s.has_rfid);
    const pinCount = slots.filter((s) => s.has_code).length;
    const rfidCount = slots.filter((s) => s.has_rfid).length;
    const maxSlots = lock.max_slots || 25;

    return html`
      <div class="detail-header">
        <h2>${lock.name}</h2>
        <div class="detail-header-actions">
          <button
            class="btn-secondary"
            @click=${this._readFromLock}
            ?disabled=${this._busy}
          >
            ${this._busy && this._busyAction ? this._busyAction : "Sync from lock"}
          </button>
        </div>
      </div>

      <div class="stats-bar">
        <span class="stat">${activeSlots.length} / ${maxSlots} users</span>
        ${pinCount > 0 ? html`<span class="stat">${pinCount} PINs</span>` : ""}
        ${rfidCount > 0 ? html`<span class="stat">${rfidCount} RFIDs</span>` : ""}
      </div>

      ${activeSlots.length > 0
        ? html`
            <table class="slots-table">
              <thead>
                <tr>
                  <th>#</th>
                  <th>Name</th>
                  <th>PIN code</th>
                  <th>RFID</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                ${activeSlots.map(
                  (slot) => html`
                    <tr class="${this._busySlot === slot.slot ? "row-busy" : ""} ${this._isDirty(slot.slot) ? "row-dirty" : ""}">
                      <td class="slot-num">
                        ${this._busySlot === slot.slot
                          ? html`<span class="spinner"></span>`
                          : slot.slot}
                      </td>
                      <td class="slot-label">
                        <input
                          type="text"
                          class="label-input"
                          .value=${slot.label || ""}
                          placeholder="unnamed"
                          @input=${(e) => this._handleLabelInput(slot, e)}
                          @keydown=${(e) => this._handleLabelKeydown(slot, e)}
                          ?disabled=${this._busy}
                        />
                      </td>
                      <td class="slot-pin">
                        <div class="pin-cell">
                          ${slot.has_code
                            ? html`
                              <div class="pin-status-row">
                                <span class="badge-sm ${slot.enabled ? "enabled" : "disabled"}">${slot.enabled ? "Active" : "Disabled"}</span>
                                <button class="btn-reveal"
                                  @click=${() => this._revealPin(slot.slot)}
                                  ?disabled=${this._busy || this._revealedPins[slot.slot] === "loading"}
                                  title="Read PIN from lock">
                                  ${this._revealedPins[slot.slot] === "loading"
                                    ? html`<span class="spinner-tiny"></span>`
                                    : html`<ha-icon icon="mdi:eye-outline"></ha-icon>`}
                                </button>
                              </div>`
                            : ""}
                          <input
                            type="text"
                            inputmode="numeric"
                            pattern="[0-9]*"
                            class="pin-input" autocomplete="off"
                            id="pin-${slot.slot}"
                            placeholder="${slot.has_code ? "new PIN" : "set PIN"}"
                            @input=${(e) => { e.target.value = e.target.value.replace(/\D/g, ""); this._handlePinInput(slot, e); }}
                            @keydown=${(e) => this._handlePinKeydown(slot, e)}
                            maxlength="10"
                            ?disabled=${this._busy}
                          />
                        </div>
                      </td>
                      <td class="slot-credential">
                        ${slot.has_rfid
                          ? html`<span class="badge-sm enabled">
                              <ha-icon icon="mdi:card-account-details-outline"></ha-icon> Active
                            </span>`
                          : html`<span class="no-rfid">—</span>`}
                      </td>
                      <td class="slot-actions">
                        ${this._isDirty(slot.slot) ? html`
                          <button class="btn-sm btn-save" @click=${() => this._commitRow(slot)} ?disabled=${this._busy}
                            title="Save changes">
                            <ha-icon icon="mdi:content-save"></ha-icon> Save
                          </button>
                        ` : html`
                          ${slot.has_code ? html`
                            <button class="btn-sm" @click=${() => this._toggleCode(slot)} ?disabled=${this._busy}
                              title="${slot.enabled ? "Disable PIN" : "Enable PIN"}">
                              ${slot.enabled
                                ? html`<ha-icon icon="mdi:pause-circle-outline"></ha-icon>`
                                : html`<ha-icon icon="mdi:play-circle-outline"></ha-icon>`}
                            </button>
                            <button class="btn-sm btn-danger" @click=${() => this._clearCode(slot.slot)} ?disabled=${this._busy}
                              title="Clear PIN">
                              <ha-icon icon="mdi:close-circle-outline"></ha-icon>
                            </button>
                          ` : ""}
                          ${slot.has_rfid ? html`
                            <button class="btn-sm btn-danger" @click=${() => this._clearRfid(slot.slot)} ?disabled=${this._busy}
                              title="Clear RFID">
                              <ha-icon icon="mdi:card-remove-outline"></ha-icon>
                            </button>
                          ` : ""}
                        `}
                      </td>
                    </tr>
                  `
                )}
              </tbody>
            </table>
          `
        : html`
            <div class="empty-state">
              <ha-icon icon="mdi:lock-open-outline"></ha-icon>
              <p>No users on this lock</p>
              <p class="empty-hint">Add a PIN code below, or click "Sync from lock" to discover existing codes and RFID tags</p>
            </div>
          `}

      ${pinCount < maxSlots ? html`
        <div class="add-row">
          <span class="add-label">Add user:</span>
          <input
            type="text"
            inputmode="numeric"
            class="add-slot-input"
            id="add-slot"
            placeholder="Slot"
            .value=${String(this._getNextFreeSlot() || "")}
            @input=${(e) => { e.target.value = e.target.value.replace(/\D/g, ""); }}
            maxlength="2"
            ?disabled=${this._busy}
          />
          <input
            type="text"
            class="label-input add-name-input"
            id="add-name"
            placeholder="Name"
            ?disabled=${this._busy}
          />
          <input
            type="text"
            inputmode="numeric"
            pattern="[0-9]*"
            class="pin-input add-pin-input"
            id="add-pin"
            placeholder="PIN (4-10 digits)"
            maxlength="10"
            @input=${(e) => { e.target.value = e.target.value.replace(/\D/g, ""); }}
            @keydown=${(e) => {
              if (e.key === "Enter") { this._submitAddCode(); return; }
              if (["Backspace","Delete","Tab","Escape","ArrowLeft","ArrowRight","Home","End"].includes(e.key)) return;
              if ((e.ctrlKey || e.metaKey) && ["a","c","v","x"].includes(e.key.toLowerCase())) return;
              if (!/^[0-9]$/.test(e.key)) e.preventDefault();
            }}
            ?disabled=${this._busy}
          />
          <button class="btn-primary btn-add-submit" @click=${this._submitAddCode} ?disabled=${this._busy}>
            ${this._busy && this._busyAction === "Adding PIN..." ? html`<span class="spinner"></span>` : "Add"}
          </button>
        </div>
      ` : html`<p class="empty" style="margin-top:16px">All ${maxSlots} slots are in use</p>`}

      <details class="settings-section">
        <summary>Lock settings</summary>
        ${this._settings ? this._renderSettings() : html`<p class="empty">Loading settings...</p>`}
      </details>
    `;
  }

  _renderSettings() {
    const s = this._settings;
    const p = this._pendingSettings;
    const lock = this._selected;

    // Firmware info (always show if available)
    const fwInfo = s.lock_firmware || s.module_build
      ? html`
        <div class="firmware-info">
          ${s.lock_firmware ? html`<span class="fw-badge">Lock FW: ${s.lock_firmware}</span>` : ""}
          ${s.module_build ? html`<span class="fw-badge">Zigbee module: ${s.module_build}</span>` : ""}
        </div>`
      : "";

    const mfrSupported = s.mfr_attrs_supported !== false;

    // Use pending value if staged, otherwise device value
    const val = (key, fallback) => p[key] ?? s[key] ?? fallback;

    const servicePinOptions = [
      { value: 0, label: "Deactivated" },
      { value: 1, label: "1 use" },
      { value: 2, label: "2 uses" },
      { value: 3, label: "5 uses" },
      { value: 4, label: "10 uses" },
      { value: 5, label: "Random PIN, 1 use" },
      { value: 6, label: "Random PIN, 24 hours" },
      { value: 7, label: "Always valid" },
      { value: 8, label: "12 hours" },
      { value: 9, label: "24 hours" },
    ];

    const lockMode = val("lock_mode", 0);
    const autoLockOn = lockMode & 1;
    const awayModeOn = lockMode & 2;
    const svcMode = val("service_pin_mode", 0);

    const volumeOptions = [
      { value: 0, label: "Silent" },
      { value: 1, label: "Low" },
      { value: 2, label: "High" },
    ];

    return html`
      ${fwInfo}
      <div class="settings-grid">
        <div class="setting-group">
          <h3>General</h3>

          <div class="setting-row">
            <label>Lock name</label>
            <input type="text" .value=${lock.name}
              @change=${(e) => { this._saveMeta(e.target.value, lock.max_slots); }} />
          </div>

          <div class="setting-row">
            <label>Audio volume</label>
            <select .value=${String(val("audio_volume", ""))}
              @change=${(e) => this._stageSetting("audio_volume", parseInt(e.target.value))}
              ?disabled=${this._busy}>
              ${volumeOptions.map(o => html`
                <option value=${o.value} ?selected=${val("audio_volume", null) === o.value}>${o.label}</option>
              `)}
            </select>
          </div>
        </div>

        <div class="setting-group">
          <h3>Access</h3>

          ${mfrSupported ? html`
            <div class="setting-row">
              <label>Master PIN can unlock</label>
              <input type="checkbox" .checked=${val("master_pin_mode", true)}
                @change=${(e) => this._stageSetting("master_pin_mode", e.target.checked)}
                ?disabled=${this._busy} />
            </div>

            <div class="setting-row">
              <label>RFID enabled</label>
              <input type="checkbox" .checked=${val("rfid_enabled", true)}
                @change=${(e) => this._stageSetting("rfid_enabled", e.target.checked)}
                ?disabled=${this._busy} />
            </div>
          ` : ""}

          <div class="setting-row">
            <label>Require PIN for RF</label>
            <input type="checkbox" .checked=${val("require_pin_for_rf", false)}
              @change=${(e) => this._stageSetting("require_pin_for_rf", e.target.checked)}
              ?disabled=${this._busy} />
          </div>

          ${!mfrSupported ? html`
            <p class="setting-hint" style="margin-top:8px">Some settings are not available on this lock firmware.</p>
          ` : ""}
        </div>

        ${mfrSupported ? html`
          <div class="setting-group">
            <h3>Lock behavior</h3>

            <div class="setting-row">
              <label>Auto-lock</label>
              <input type="checkbox" .checked=${!!autoLockOn}
                @change=${(e) => this._stageSettingLockModeBit(1, e.target.checked)}
                ?disabled=${this._busy} />
            </div>

            <div class="setting-row">
              <div>
                <label>Away mode</label>
                <div class="setting-hint">Requires both PIN and RFID to unlock</div>
              </div>
              <input type="checkbox" .checked=${!!awayModeOn}
                @change=${(e) => this._stageSettingLockModeBit(2, e.target.checked)}
                ?disabled=${this._busy} />
            </div>

            <div class="setting-row">
              <label>Auto-relock</label>
              <input type="checkbox" .checked=${val("relock_enabled", false)}
                @change=${(e) => this._stageSetting("relock_enabled", e.target.checked)}
                ?disabled=${this._busy} />
              <span class="setting-hint">Re-locks if unlocked but the door was never opened</span>
            </div>

            <div class="setting-row">
              <label>Service PIN</label>
              <select .value=${String(svcMode)}
                @change=${(e) => this._stageSetting("service_pin_mode", parseInt(e.target.value))}
                ?disabled=${this._busy}>
                ${servicePinOptions.map(o => html`
                  <option value=${o.value} ?selected=${svcMode === o.value}>${o.label}</option>
                `)}
              </select>
            </div>
            ${svcMode === 5 || svcMode === 6 ? html`
              <div class="setting-info">
                <p>To see the random PIN, enter <strong>[Master PIN] + [*] + [8]</strong> on the lock keypad.</p>
              </div>
            ` : ""}
          </div>
        ` : ""}

        <div class="setting-group">
          <h3>Device info</h3>
          <div class="info-row"><span>PIN slots:</span> <span>${s.num_pin_slots ?? "?"}</span></div>
          <div class="info-row"><span>RFID slots:</span> <span>${s.num_rfid_slots ?? "?"}</span></div>
          <div class="info-row"><span>PIN length:</span> <span>${s.min_pin_len ?? "?"}–${s.max_pin_len ?? "?"} digits</span></div>
          <div class="info-row"><span>IEEE:</span> <span class="mono">${s.ieee ?? "?"}</span></div>
        </div>
      </div>

      ${this._hasSettingsChanges ? html`
        <div class="settings-save-bar">
          <span>You have unsaved changes</span>
          <button class="btn-primary" @click=${this._commitSettings} ?disabled=${this._busy}>
            ${this._busy && this._busyAction === "Saving settings..." ? html`<span class="spinner"></span>` : "Save settings"}
          </button>
        </div>
      ` : ""}
    `;
  }

  static get styles() {
    return css`
      :host {
        display: block;
        padding: 16px;
        font-family: var(--primary-font-family, Roboto, sans-serif);
        color: var(--primary-text-color, #333);
        background: var(--primary-background-color, #fafafa);
        min-height: 100vh;
      }

      .container { max-width: 1200px; margin: 0 auto; }

      .header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 16px;
        padding-bottom: 12px;
        border-bottom: 1px solid var(--divider-color, #e0e0e0);
      }
      .header h1 { margin: 0; font-size: 24px; }
      .header-actions button {
        padding: 8px 16px;
        border-radius: 8px;
        border: 1px solid var(--divider-color, #ccc);
        background: var(--card-background-color, #fff);
        cursor: pointer;
      }

      .error {
        background: var(--error-color, #db4437);
        color: white;
        padding: 8px 16px;
        border-radius: 8px;
        margin-bottom: 12px;
      }

      .layout { display: grid; gap: 16px; }
      .desktop .layout { grid-template-columns: 240px 1fr; }
      .mobile .layout { grid-template-columns: 1fr; }

      .lock-list {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 12px;
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.1));
      }
      .lock-list h2 { margin: 0 0 8px; font-size: 16px; }
      .lock-item {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 10px 12px;
        border-radius: 8px;
        cursor: pointer;
        transition: background 0.15s;
      }
      .lock-item:hover { background: var(--secondary-background-color, #f0f0f0); }
      .lock-item.selected {
        background: var(--primary-color, #03a9f4);
        color: white;
      }
      .lock-item.selected ha-icon { color: white; }

      .lock-detail {
        background: var(--card-background-color, #fff);
        border-radius: 12px;
        padding: 16px;
        box-shadow: var(--ha-card-box-shadow, 0 2px 6px rgba(0,0,0,0.1));
      }

      .detail-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
      }
      .detail-header h2 { margin: 0; }
      .detail-header-actions { display: flex; gap: 8px; }

      .stats-bar {
        display: flex;
        gap: 16px;
        margin-bottom: 16px;
        color: var(--secondary-text-color, #666);
        font-size: 13px;
      }
      .stat {
        background: var(--secondary-background-color, #f0f0f0);
        padding: 4px 10px;
        border-radius: 12px;
      }

      .empty-state {
        text-align: center;
        padding: 40px 16px;
        color: var(--secondary-text-color, #999);
      }
      .empty-state ha-icon {
        --mdc-icon-size: 48px;
        margin-bottom: 12px;
        opacity: 0.4;
      }
      .empty-state p { margin: 4px 0; }
      .empty-hint { font-size: 13px; }

      .add-row {
        display: flex;
        align-items: center;
        gap: 8px;
        margin-top: 16px;
        padding: 12px;
        background: var(--secondary-background-color, #f5f5f5);
        border-radius: 8px;
        flex-wrap: wrap;
      }
      .add-label { font-size: 13px; font-weight: 500; color: var(--secondary-text-color); }
      .add-slot-input {
        width: 45px; padding: 6px 8px;
        border: 1px solid var(--divider-color, #ccc); border-radius: 4px;
        background: var(--card-background-color, #fff); color: var(--primary-text-color);
        font-size: 14px; text-align: center;
      }
      .add-name-input {
        width: 120px; padding: 6px 8px;
        border: 1px solid var(--divider-color, #ccc) !important;
        border-radius: 4px !important;
        background: var(--card-background-color, #fff) !important;
      }
      .add-pin-input {
        width: 160px; padding: 6px 8px;
        border: 1px solid var(--divider-color, #ccc) !important;
        border-radius: 4px !important;
        background: var(--card-background-color, #fff);
      }
      .btn-add-submit { padding: 6px 16px; min-width: 60px; }

      .settings-section {
        margin-top: 16px;
      }
      .settings-section summary {
        cursor: pointer;
        color: var(--secondary-text-color, #666);
        font-size: 13px;
        padding: 8px 0;
      }

      .firmware-info {
        display: flex; gap: 8px; padding: 8px 0; flex-wrap: wrap;
      }
      .fw-badge {
        font-size: 12px; padding: 2px 8px; border-radius: 4px;
        background: var(--secondary-background-color, #f5f5f5);
        color: var(--secondary-text-color, #888);
      }
      .settings-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
        gap: 16px;
        padding: 12px 0;
      }
      .setting-group {
        background: var(--secondary-background-color, #f5f5f5);
        border-radius: 8px;
        padding: 12px;
      }
      .setting-group h3 {
        margin: 0 0 10px;
        font-size: 14px;
        color: var(--primary-text-color);
      }
      .setting-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 6px 0;
        border-bottom: 1px solid var(--divider-color, #e0e0e0);
      }
      .setting-row:last-child { border-bottom: none; }
      .setting-row label { font-size: 13px; }
      .setting-row select, .setting-row input[type="text"] {
        padding: 4px 8px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 4px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        font-size: 13px;
      }
      .setting-row input[type="text"] { width: 140px; }
      .setting-row input[type="checkbox"] {
        width: 18px; height: 18px; cursor: pointer;
      }
      .settings-save-bar {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-top: 12px;
        padding: 10px 14px;
        background: color-mix(in srgb, var(--primary-color, #03a9f4) 12%, transparent);
        border-radius: 8px;
        font-size: 13px;
        color: var(--primary-text-color);
      }
      .setting-info {
        margin-top: 4px;
        padding: 8px 10px;
        background: color-mix(in srgb, var(--primary-color, #03a9f4) 8%, transparent);
        border-radius: 6px;
        font-size: 12px;
        color: var(--secondary-text-color, #666);
        line-height: 1.4;
      }
      .setting-info p { margin: 0; }
      .setting-hint {
        font-size: 11px;
        color: var(--secondary-text-color, #888);
        margin-top: 2px;
      }
      .info-row {
        display: flex;
        justify-content: space-between;
        padding: 4px 0;
        font-size: 13px;
        color: var(--secondary-text-color, #666);
      }
      .mono { font-family: monospace; font-size: 12px; }

      .btn-secondary {
        padding: 8px 16px;
        border-radius: 8px;
        border: 1px solid var(--divider-color, #ccc);
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        cursor: pointer;
        font-size: 13px;
      }
      .btn-secondary:hover { background: var(--secondary-background-color, #f0f0f0); }
      .btn-secondary:disabled { opacity: 0.5; cursor: not-allowed; }

      .meta-section {
        display: flex;
        gap: 12px;
        align-items: center;
        margin-bottom: 16px;
        padding: 12px;
        background: var(--secondary-background-color, #f5f5f5);
        border-radius: 8px;
        flex-wrap: wrap;
      }
      .meta-section label { display: flex; align-items: center; gap: 4px; font-size: 14px; }
      .meta-section input {
        padding: 4px 8px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 4px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
      }
      .meta-section input[type="number"] { width: 60px; }
      .meta-section input[type="text"] { width: 150px; }

      .slots-table { width: 100%; border-collapse: collapse; }
      .slots-table th {
        text-align: left;
        padding: 8px;
        border-bottom: 2px solid var(--divider-color, #e0e0e0);
        font-size: 13px;
        text-transform: uppercase;
        color: var(--secondary-text-color, #666);
      }
      .slots-table td { padding: 8px; border-bottom: 1px solid var(--divider-color, #eee); }

      .slot-num { font-weight: 600; width: 40px; }
      .slot-label { min-width: 100px; }
      .label-input {
        border: none;
        border-bottom: 1px solid transparent;
        background: transparent;
        color: var(--primary-text-color);
        font-size: 14px;
        padding: 4px 2px;
        width: 100%;
        outline: none;
        transition: border-color 0.15s;
      }
      .label-input:hover { border-bottom-color: var(--divider-color, #ccc); }
      .label-input:focus { border-bottom-color: var(--primary-color, #03a9f4); }
      .label-input::placeholder { color: var(--secondary-text-color, #999); font-style: italic; }

      .badge {
        display: inline-block;
        padding: 2px 8px;
        border-radius: 12px;
        font-size: 12px;
        font-weight: 500;
      }
      .badge.enabled { background: #4caf50; color: white; }
      .badge.disabled { background: #ff9800; color: white; }
      .badge.empty { background: transparent; color: var(--divider-color, #ccc); }
      .badge ha-icon { --mdc-icon-size: 14px; margin-right: 2px; vertical-align: middle; }

      .slot-credential { white-space: nowrap; }
      .no-rfid { color: var(--divider-color, #ccc); }

      .pin-cell { display: flex; flex-direction: column; gap: 4px; }
      .pin-input {
        border: none;
        border-bottom: 1px solid transparent;
        background: transparent;
        color: var(--primary-text-color);
        font-size: 14px;
        font-family: monospace;
        padding: 4px 2px;
        width: 130px;
        outline: none;
        letter-spacing: 2px;
        transition: border-color 0.15s;
      }
      .pin-input:hover { border-bottom-color: var(--divider-color, #ccc); }
      .pin-input:focus { border-bottom-color: var(--primary-color, #03a9f4); }
      .pin-input::placeholder {
        color: var(--secondary-text-color, #999);
        font-style: italic;
        font-family: var(--primary-font-family, Roboto, sans-serif);
        letter-spacing: normal;
        font-size: 12px;
      }

      .badge-sm {
        display: inline-block;
        padding: 1px 6px;
        border-radius: 8px;
        font-size: 11px;
        font-weight: 500;
      }
      .badge-sm.enabled { background: #4caf50; color: white; }
      .badge-sm.disabled { background: #ff9800; color: white; }
      .badge-sm ha-icon { --mdc-icon-size: 12px; vertical-align: middle; }
      .pin-status-row { display: flex; align-items: center; gap: 4px; }
      .btn-reveal {
        border: none; background: transparent; cursor: pointer;
        padding: 2px; color: var(--secondary-text-color, #888);
        display: inline-flex; align-items: center; justify-content: center;
        border-radius: 4px; transition: color 0.15s;
      }
      .btn-reveal:hover { color: var(--primary-color, #03a9f4); }
      .btn-reveal:disabled { opacity: 0.5; cursor: not-allowed; }
      .btn-reveal ha-icon { --mdc-icon-size: 16px; }
      .spinner-tiny {
        display: inline-block; width: 12px; height: 12px;
        border: 1.5px solid var(--divider-color, #ccc);
        border-top-color: var(--primary-color, #03a9f4); border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }

      .slot-actions { white-space: nowrap; }
      .slot-actions ha-icon { --mdc-icon-size: 16px; }

      tr.row-busy { opacity: 0.6; }
      tr.row-busy td:first-child { position: relative; }

      @keyframes spin {
        to { transform: rotate(360deg); }
      }
      .spinner {
        display: inline-block;
        width: 14px;
        height: 14px;
        border: 2px solid var(--divider-color, #ccc);
        border-top-color: var(--primary-color, #03a9f4);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
      }

      button {
        cursor: pointer;
        font-size: 13px;
      }
      .btn-primary {
        padding: 8px 16px;
        background: var(--primary-color, #03a9f4);
        color: white;
        border: none;
        border-radius: 8px;
        font-weight: 500;
      }
      .btn-primary:disabled { opacity: 0.5; cursor: not-allowed; }
      .btn-sm {
        padding: 4px 10px;
        border: 1px solid var(--divider-color, #ccc);
        border-radius: 6px;
        background: var(--card-background-color, #fff);
        color: var(--primary-text-color);
        margin-right: 4px;
      }
      .btn-sm:hover { background: var(--secondary-background-color, #f0f0f0); }
      .btn-sm:disabled { opacity: 0.5; cursor: not-allowed; }
      .btn-danger { color: var(--error-color, #db4437); border-color: var(--error-color, #db4437); }
      .btn-danger:hover { background: var(--error-color, #db4437); color: white; }
      .btn-save {
        background: var(--primary-color, #03a9f4) !important;
        color: white !important;
        border-color: var(--primary-color, #03a9f4) !important;
      }
      .btn-save ha-icon { --mdc-icon-size: 14px; }

      tr.row-dirty {
        background: color-mix(in srgb, var(--primary-color, #03a9f4) 8%, transparent);
      }

      .empty { color: var(--secondary-text-color, #999); font-style: italic; }

      @media (max-width: 600px) {
        .meta-section { flex-direction: column; align-items: stretch; }
        .slot-actions { display: flex; flex-wrap: wrap; gap: 4px; }
      }
    `;
  }
}

if (!customElements.get("ha-idlock-panel")) {
  customElements.define("ha-idlock-panel", HaIdlockPanel);
}
