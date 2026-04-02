import { Composition } from "remotion";

const CleanSweep = () => null;

export const RemotionRoot = () => {
  return (
    <Composition
      id="CleanSweep"
      component={CleanSweep}
      durationInFrames={36}
      fps={30}
      width={320}
      height={320}
    />
  );
};
