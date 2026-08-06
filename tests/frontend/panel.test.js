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
