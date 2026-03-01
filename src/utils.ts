import type { Program } from "./types";

export type AppTab = "dashboard" | "hacktivity" | "updates";

export interface HashRoute {
  tab: AppTab;
  programId: string | null;
}

const usdFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

export function formatUsd(value: number): string {
  if (!Number.isFinite(value) || value <= 0) {
    return "-";
  }
  return usdFormatter.format(value);
}

export function formatDate(value: string): string {
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return "Unknown";
  }
  return parsed.toLocaleDateString("en-US", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}

export function daysBetween(fromIso: string, toIso: string): number {
  const from = new Date(fromIso);
  const to = new Date(toIso);
  if (Number.isNaN(from.getTime()) || Number.isNaN(to.getTime())) {
    return Number.MAX_SAFE_INTEGER;
  }
  const difference = Math.max(0, to.getTime() - from.getTime());
  return Math.floor(difference / (1000 * 60 * 60 * 24));
}

export function formatAge(fromIso: string, toIso: string): string {
  const days = daysBetween(fromIso, toIso);
  if (days === Number.MAX_SAFE_INTEGER) {
    return "unknown";
  }
  if (days <= 1) {
    return "today";
  }
  if (days < 30) {
    return `${days}d ago`;
  }
  const months = Math.floor(days / 30);
  if (months < 12) {
    return `${months}mo ago`;
  }
  const years = Math.floor(months / 12);
  return `${years}y ago`;
}

export function isNewProgram(program: Program, generatedAt: string, thresholdDays = 30): boolean {
  return daysBetween(program.createdAt, generatedAt) <= thresholdDays;
}

export function getRouteFromHash(hash: string): HashRoute {
  const cleanHash = hash.startsWith("#") ? hash.slice(1) : hash;
  const [rawPath, rawQuery] = cleanHash.split("?");
  const segments = rawPath.split("/").filter(Boolean);
  const query = new URLSearchParams(rawQuery ?? "");
  const queryTab = query.get("tab");

  const tabFromPath: AppTab =
    segments[0] === "hacktivity"
      ? "hacktivity"
      : segments[0] === "updates"
        ? "updates"
        : "dashboard";
  const tab: AppTab =
    queryTab === "hacktivity" || queryTab === "updates" ? queryTab : tabFromPath;

  if (segments.length === 2 && segments[0] === "program") {
    return { tab, programId: decodeURIComponent(segments[1]) };
  }

  if (segments.length === 3 && segments[0] === "hacktivity" && segments[1] === "program") {
    return { tab: "hacktivity", programId: decodeURIComponent(segments[2]) };
  }
  if (segments.length === 3 && segments[0] === "updates" && segments[1] === "program") {
    return { tab: "updates", programId: decodeURIComponent(segments[2]) };
  }

  return { tab, programId: null };
}

export function getProgramIdFromHash(hash: string): string | null {
  return getRouteFromHash(hash).programId;
}

export function getTabFromHash(hash: string): AppTab {
  return getRouteFromHash(hash).tab;
}

export function toTabHash(tab: AppTab): string {
  if (tab === "hacktivity") {
    return "#/hacktivity";
  }
  if (tab === "updates") {
    return "#/updates";
  }
  return "#/";
}

export function toProgramHash(programId: string, tab: AppTab = "dashboard"): string {
  const base = `#/program/${encodeURIComponent(programId)}`;
  if (tab === "hacktivity") {
    return `${base}?tab=hacktivity`;
  }
  if (tab === "updates") {
    return `${base}?tab=updates`;
  }
  return base;
}
