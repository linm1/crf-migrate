import { Composition } from "remotion";
import { CleanSweep, type CleanSweepProps } from "./CleanSweep";

export const RemotionRoot = () => {
  return (
    <Composition
      id="CleanSweep"
      component={CleanSweep}
      durationInFrames={36}
      fps={30}
      width={320}
      height={320}
      defaultProps={
        {
          backgroundColor: "#24191B",
          iconSize: 132,
          loopSeconds: 1.2,
          showSpark: true,
        } satisfies CleanSweepProps
      }
    />
  );
};
