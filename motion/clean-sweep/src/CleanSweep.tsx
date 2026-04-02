import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { getSweepState } from "./cleanSweepMotion";

export type CleanSweepProps = {
  backgroundColor: string;
  iconSize: number;
  loopSeconds: number;
  showSpark: boolean;
};

export const CleanSweep: React.FC<CleanSweepProps> = ({
  backgroundColor,
  iconSize,
  loopSeconds,
  showSpark,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const durationInFrames = Math.round(loopSeconds * fps);
  const localFrame = frame % durationInFrames;
  const state = getSweepState({ frame: localFrame, fps, durationInFrames });

  const bandTranslateX = state.bandCenterX;
  const sparkleScale = interpolate(state.sparkOpacity, [0, 1], [0.7, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.out(Easing.quad),
  });

  return (
    <AbsoluteFill
      style={{
        backgroundColor,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      <div
        style={{
          width: iconSize,
          height: iconSize,
          position: "relative",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
        }}
      >
        <Img
          src={staticFile("icon.png")}
          style={{
            width: iconSize,
            height: iconSize,
          }}
        />
        <div
          style={{
            position: "absolute",
            top: -12,
            left: bandTranslateX,
            width: 24,
            height: iconSize + 24,
            transform: "rotate(-32deg)",
            borderRadius: 999,
            opacity: state.opacity,
            background:
              "linear-gradient(180deg, rgba(255,255,255,0) 0%, rgba(255,255,255,0.92) 50%, rgba(255,255,255,0) 100%)",
            mixBlendMode: "screen",
          }}
        />
        {showSpark ? (
          <div
            style={{
              position: "absolute",
              top: 18,
              right: 28,
              width: 9,
              height: 9,
              borderRadius: 999,
              backgroundColor: "#FF8A00",
              opacity: state.sparkOpacity,
              transform: `scale(${sparkleScale})`,
            }}
          />
        ) : null}
      </div>
    </AbsoluteFill>
  );
};
