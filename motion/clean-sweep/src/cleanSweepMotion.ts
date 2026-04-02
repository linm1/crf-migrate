export type SweepStateInput = {
  frame: number;
  fps: number;
  durationInFrames: number;
};

export type SweepState = {
  progress: number;
  bandCenterX: number;
  opacity: number;
  sparkOpacity: number;
};

const clamp = (value: number, min: number, max: number) =>
  Math.min(max, Math.max(min, value));

const easeInOut = (value: number) => {
  return value < 0.5
    ? 4 * value * value * value
    : 1 - Math.pow(-2 * value + 2, 3) / 2;
};

export const getSweepState = ({
  frame,
  durationInFrames,
}: SweepStateInput): SweepState => {
  const progress = clamp(frame / durationInFrames, 0, 1);
  const eased = easeInOut(progress);
  const bandCenterX = -40 + eased * 360;
  const opacity = 0.55 + (1 - Math.abs(eased - 0.5) * 2) * 0.3;
  const sparkOpacity = clamp((eased - 0.58) / 0.18, 0, 1);

  return {
    progress,
    bandCenterX,
    opacity,
    sparkOpacity,
  };
};
