import { afterEach, describe, expect, it, vi } from "vitest";

import { HaIdlockPanel } from "../../custom_components/ha_idlock/frontend/ha_idlock_panel.js";

afterEach(() => {
  vi.useRealTimers();
});
describe("PIN privacy and request races", () => {
  it("discards a PIN response after the selected lock changes", async () => {
    let resolve;
    const response = new Promise((done) => { resolve = done; });
    const panel = new HaIdlockPanel();
    panel._selected = { device_ieee: "lock-a" };
    panel._ws = vi.fn(() => response);

    const pending = panel._revealPin(1);
    panel._selectLock({ device_ieee: "lock-b" });
    resolve({ code: "1234" });
    await pending;

    expect(panel._revealedPins).toEqual({});
    expect(panel._visiblePins).toEqual({});
  });

  it("automatically removes revealed PIN data after 30 seconds", async () => {
    vi.useFakeTimers();
    const panel = new HaIdlockPanel();
    panel._selected = { device_ieee: "lock-a" };
    panel._ws = vi.fn().mockResolvedValue({ code: "1234" });

    await panel._revealPin(1);
    expect(panel._revealedPins[1]).toBe("1234");
    expect(panel._visiblePins[1]).toBe(true);

    vi.advanceTimersByTime(30000);
    expect(panel._revealedPins).toEqual({});
    expect(panel._visiblePins).toEqual({});
  });
});

describe("overlapping PIN reveals", () => {
  it("does not leave an earlier slot stuck loading", async () => {
    const resolvers = {};
    const panel = new HaIdlockPanel();
    panel._selected = { device_ieee: "lock-a" };
    panel._ws = vi.fn((_type, { slot }) => new Promise((done) => { resolvers[slot] = done; }));

    const first = panel._revealPin(1);
    const second = panel._revealPin(2);
    resolvers[1]({ code: "1111" });
    resolvers[2]({ code: "2222" });
    await Promise.all([first, second]);

    expect(panel._revealedPins).toEqual({ 1: "1111", 2: "2222" });
    panel._clearPinMemory();
  });
});

describe("adding a user", () => {
  it("asks before replacing an existing PIN", async () => {
    const panel = new HaIdlockPanel();
    panel._selected = { device_ieee: "lock-a", max_slots: 25, slots: { 3: { slot: 3, has_code: true, label: "Alice" } } };
    const inputs = { "#add-slot": { value: "3" }, "#add-name": { value: "Bob" }, "#add-pin": { value: "1234" } };
    Object.defineProperty(panel, "shadowRoot", { value: { querySelector: (sel) => inputs[sel] } });
    panel._ws = vi.fn().mockResolvedValue([]);

    await panel._submitAddCode();

    expect(panel._ws).not.toHaveBeenCalled();
    expect(panel._confirmation).toMatchObject({ kind: "overwrite", slot: 3, code: "1234", label: "Bob" });
  });
});
