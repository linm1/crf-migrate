import { spawnSync } from "node:child_process";

const steps = [
  ["npm", ["run", "render:still"]],
  ["npm", ["run", "render:webm"]],
];

for (const [command, args] of steps) {
  const result = spawnSync(command, args, {
    stdio: "inherit",
    shell: true,
  });

  if (result.status !== 0) {
    process.exit(result.status ?? 1);
  }
}
