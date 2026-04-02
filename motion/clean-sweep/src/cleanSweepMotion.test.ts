import { describe, expect, it } from "vitest";
import { getSweepState } from "./cleanSweepMotion";
import { cleanSweepCompositionConfig } from "./Root";

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

  it("keeps the sweep ordered from start to end and finishes bright", () => {
    const start = getSweepState({
      frame: 0,
      fps: 30,
      durationInFrames: 36,
    });
    const mid = getSweepState({
      frame: 18,
      fps: 30,
      durationInFrames: 36,
    });
    const end = getSweepState({
      frame: 36,
      fps: 30,
      durationInFrames: 36,
    });
    const pastEnd = getSweepState({
      frame: 40,
      fps: 30,
      durationInFrames: 36,
    });

    expect(start.bandCenterX).toBeLessThan(mid.bandCenterX);
    expect(mid.bandCenterX).toBeLessThan(end.bandCenterX);
    expect(mid.opacity).toBeGreaterThan(start.opacity);
    expect(mid.opacity).toBeGreaterThan(end.opacity);
    expect(end.progress).toBe(1);
    expect(end.sparkOpacity).toBe(1);
    expect(end.bandCenterX).toBeGreaterThan(300);
    expect(pastEnd.sparkOpacity).toBeGreaterThanOrEqual(0);
    expect(pastEnd.sparkOpacity).toBeLessThanOrEqual(1);
  });
});

describe("composition defaults", () => {
  it("uses a loop length matching the approved design", () => {
    const { durationInFrames, fps, defaultProps } = cleanSweepCompositionConfig;
    const state = getSweepState({
      frame: Math.floor(durationInFrames / 2),
      fps,
      durationInFrames,
    });

    expect(defaultProps.loopSeconds).toBe(1.2);
    expect(fps).toBe(30);
    expect(durationInFrames).toBe(Math.round(defaultProps.loopSeconds * fps));
    expect(state.bandCenterX).toBeGreaterThan(120);
  });
});
