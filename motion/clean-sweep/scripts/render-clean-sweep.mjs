import { execFileSync } from "node:child_process";

const run = (args) => {
  execFileSync("npx", args, {
    stdio: "inherit",
    shell: process.platform === "win32",
  });
};

run(["remotion", "still", "src/index.ts", "CleanSweep", "out/clean-sweep.png"]);
run([
  "remotion",
  "render",
  "src/index.ts",
  "CleanSweep",
  "out/clean-sweep.webm",
  "--image-format=png",
  "--pixel-format=yuva420p",
  "--codec=vp9",
]);
