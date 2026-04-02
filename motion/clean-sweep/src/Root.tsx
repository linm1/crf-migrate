import { Composition } from "remotion";

// Placeholder root component until Task 2 adds the actual motion module.
const CleanSweepPlaceholder = () => null;

export const RemotionRoot = () => {
  return (
    <Composition
      id="CleanSweep"
      component={CleanSweepPlaceholder}
      durationInFrames={36}
      fps={30}
      width={320}
      height={320}
    />
  );
};
