import { describe, expect, it } from "vitest";
import { getSweepState } from "./cleanSweepMotion";

describe("getSweepState", () => {
  it("starts the band off-canvas on the left", () => {
    const state = getSweepState({
      frame: 0,
      fps: 30,
      durationInFrames: 36,
    });

    expect(state.bandCenterX).toBeLessThan(0);
  });

  it("moves the band across the icon by mid-loop", () => {
    const state = getSweepState({
      frame: 18,
      fps: 30,
      durationInFrames: 36,
    });

    expect(state.bandCenterX).toBeGreaterThan(120);
    expect(state.opacity).toBeGreaterThan(0.6);
  });

  it("clamps progress and keeps spark opacity in range", () => {
    const state = getSweepState({
      frame: 40,
      fps: 30,
      durationInFrames: 36,
    });

    expect(state.progress).toBe(1);
    expect(state.sparkOpacity).toBeGreaterThanOrEqual(0);
    expect(state.sparkOpacity).toBeLessThanOrEqual(1);
  });
});
