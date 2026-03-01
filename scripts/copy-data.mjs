import { cpSync, existsSync, mkdirSync, rmSync } from "node:fs";
import { resolve } from "node:path";

const rootDir = process.cwd();
const sourceDir = resolve(rootDir, "data");
const distDir = resolve(rootDir, "dist");
const targetDir = resolve(distDir, "data");

if (!existsSync(sourceDir)) {
  throw new Error(`Missing source data directory: ${sourceDir}`);
}

mkdirSync(distDir, { recursive: true });
if (existsSync(targetDir)) {
  rmSync(targetDir, { recursive: true, force: true });
}

cpSync(sourceDir, targetDir, { recursive: true });

console.log(`[build] copied ${sourceDir} -> ${targetDir}`);
