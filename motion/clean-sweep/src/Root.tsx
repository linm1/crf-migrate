import { Composition } from "remotion";
import { CleanSweep, type CleanSweepProps } from "./CleanSweep";

const loopSeconds = 1.2;
const fps = 30;

export const cleanSweepCompositionConfig = {
  id: "CleanSweep",
  durationInFrames: Math.round(loopSeconds * fps),
  fps: 30,
  width: 320,
  height: 320,
  defaultProps: {
    backgroundColor: "#24191B",
    iconSize: 132,
    loopSeconds,
    showSpark: true,
  } satisfies CleanSweepProps,
} as const;

export const RemotionRoot = () => {
  return (
    <Composition
      id={cleanSweepCompositionConfig.id}
      component={CleanSweep}
      durationInFrames={cleanSweepCompositionConfig.durationInFrames}
      fps={cleanSweepCompositionConfig.fps}
      width={cleanSweepCompositionConfig.width}
      height={cleanSweepCompositionConfig.height}
      defaultProps={cleanSweepCompositionConfig.defaultProps}
    />
  );
};
