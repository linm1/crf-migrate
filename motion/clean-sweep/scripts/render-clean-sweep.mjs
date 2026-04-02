import { execFileSync } from "node:child_process";

const run = (scriptName) => {
  execFileSync("npm", ["run", scriptName], {
    stdio: "inherit",
    shell: process.platform === "win32",
  });
};

run("render:still");
run("render:webm");
