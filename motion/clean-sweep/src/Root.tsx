import { Composition } from "remotion";
import { CleanSweep, type CleanSweepProps } from "./CleanSweep";

export const RemotionRoot = () => {
  const loopSeconds = 1.2;
  const fps = 30;

  return (
    <Composition
      id="CleanSweep"
      component={CleanSweep}
      durationInFrames={Math.round(loopSeconds * fps)}
      fps={fps}
      width={320}
      height={320}
      defaultProps={
        {
          backgroundColor: "#24191B",
          iconSize: 132,
          loopSeconds,
          showSpark: true,
        } satisfies CleanSweepProps
      }
    />
  );
};
