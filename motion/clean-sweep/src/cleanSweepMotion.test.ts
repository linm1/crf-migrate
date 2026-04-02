import { describe, expect, it } from "vitest";
import { createRequire } from "node:module";

const require = createRequire(import.meta.url);

describe("getSweepState", () => {
  it("returns a clamped progress value at the start of the loop", () => {
    const { getSweepState } = require("./cleanSweepMotion");
    const state = getSweepState({
      frame: 0,
      fps: 30,
      durationInFrames: 36,
    });

    expect(state.progress).toBeGreaterThanOrEqual(0);
    expect(state.progress).toBeLessThanOrEqual(1);
  });
});
