import type { BountyType, Program } from "./types";
import { isNewProgram } from "./utils";

export type ScopeFilterKey = "wildcard" | "exact" | "url" | "auth";
export type SortKey = "priority" | "maxBounty" | "updated" | "submissions";
export type PresetId =
  | "none"
  | "high-value"
  | "new-programs"
  | "api-focused"
  | "wildcard-only"
  | "india-cash";

export interface FilterOptions {
  platforms: string[];
  bountyTypes: BountyType[];
  assetTypes: string[];
}

export interface FilterState {
  query: string;
  platforms: string[];
  bountyTypes: BountyType[];
  scopeTypes: ScopeFilterKey[];
  assetTypes: string[];
  minBounty: number;
  indiaOnly: boolean;
  activelyHuntedOnly: boolean;
  preset: PresetId;
  sortBy: SortKey;
}

export const SCOPE_FILTERS: Array<{ key: ScopeFilterKey; label: string }> = [
  { key: "wildcard", label: "Wildcard domains" },
  { key: "exact", label: "Exact domains" },
  { key: "url", label: "URL / path scope" },
  { key: "auth", label: "Auth / OAuth flows" },
];

export const PRESET_FILTERS: Array<{ id: PresetId; label: string; description: string }> = [
  { id: "high-value", label: "High Value Programs", description: "Cash programs with high payouts." },
  { id: "new-programs", label: "New Programs", description: "Recently added programs." },
  { id: "api-focused", label: "API-Focused Programs", description: "Programs with API assets." },
  { id: "wildcard-only", label: "Wildcard Only", description: "Programs focused only on wildcard domains." },
  { id: "india-cash", label: "India + Cash Programs", description: "India-relevant programs with cash rewards." },
];

const bountyOrder: BountyType[] = ["Cash", "Points", "Swag", "No bounty"];
const assetOrder = ["Web", "API", "Mobile", "Cloud", "IoT", "Source code"];

export function buildFilterOptions(programs: Program[]): FilterOptions {
  const platforms = Array.from(new Set(programs.map((program) => program.platform))).sort((a, b) =>
    a.localeCompare(b)
  );

  const bountyTypes = bountyOrder.filter((bountyType) =>
    programs.some((program) => program.bountyType === bountyType)
  );

  const assetTypes = assetOrder.filter((assetType) =>
    programs.some((program) => program.assetTypes.includes(assetType as Program["assetTypes"][number]))
  );

  return { platforms, bountyTypes, assetTypes };
}

export function buildDefaultFilters(options: FilterOptions): FilterState {
  return {
    query: "",
    platforms: [...options.platforms],
    bountyTypes: [...options.bountyTypes],
    scopeTypes: SCOPE_FILTERS.map((scopeFilter) => scopeFilter.key),
    assetTypes: [...options.assetTypes],
    minBounty: 0,
    indiaOnly: false,
    activelyHuntedOnly: false,
    preset: "none",
    sortBy: "priority",
  };
}

export function getPlatformCounts(programs: Program[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const program of programs) {
    counts[program.platform] = (counts[program.platform] ?? 0) + 1;
  }
  return counts;
}

function matchesScopeTypes(program: Program, selected: ScopeFilterKey[]): boolean {
  if (selected.length === 0 || selected.length === SCOPE_FILTERS.length) {
    return true;
  }

  const map: Record<ScopeFilterKey, boolean> = {
    wildcard: program.scopeParsed.wildcardDomains.length > 0,
    exact: program.scopeParsed.exactDomains.length > 0,
    url: program.scopeParsed.urlPaths.length > 0,
    auth: program.scopeParsed.authFlows.length > 0,
  };

  return selected.some((filterKey) => map[filterKey]);
}

function matchesPreset(program: Program, preset: PresetId, generatedAt: string): boolean {
  switch (preset) {
    case "high-value":
      return program.bountyType === "Cash" && program.bountyMaxUsd >= 5000;
    case "new-programs":
      return isNewProgram(program, generatedAt, 30);
    case "api-focused":
      return program.assetTypes.includes("API");
    case "wildcard-only":
      return (
        program.scopeParsed.wildcardDomains.length > 0 &&
        program.scopeParsed.exactDomains.length === 0 &&
        program.scopeParsed.urlPaths.length === 0
      );
    case "india-cash":
      return program.isIndiaRelevant && program.bountyType === "Cash";
    case "none":
    default:
      return true;
  }
}

function sortPrograms(programs: Program[], sortBy: SortKey): Program[] {
  const sorted = [...programs];
  sorted.sort((left, right) => {
    if (sortBy === "submissions") {
      const leftCount = left.submissionCount ?? -1;
      const rightCount = right.submissionCount ?? -1;
      if (rightCount !== leftCount) {
        return rightCount - leftCount;
      }
      const leftRecent = left.submissionsLast7d ?? -1;
      const rightRecent = right.submissionsLast7d ?? -1;
      return rightRecent - leftRecent || right.priorityScore - left.priorityScore;
    }

    if (sortBy === "maxBounty") {
      return right.bountyMaxUsd - left.bountyMaxUsd || right.priorityScore - left.priorityScore;
    }

    if (sortBy === "updated") {
      const leftTime = new Date(left.lastUpdated).getTime();
      const rightTime = new Date(right.lastUpdated).getTime();
      return rightTime - leftTime || right.priorityScore - left.priorityScore;
    }

    return right.priorityScore - left.priorityScore || right.bountyMaxUsd - left.bountyMaxUsd;
  });
  return sorted;
}

export function filterPrograms(
  programs: Program[],
  filters: FilterState,
  generatedAt: string
): Program[] {
  const query = filters.query.trim().toLowerCase();
  const selectedPlatforms = new Set(filters.platforms);
  const selectedBountyTypes = new Set(filters.bountyTypes);
  const selectedAssetTypes = new Set(filters.assetTypes);

  const results = programs.filter((program) => {
    if (selectedPlatforms.size > 0 && !selectedPlatforms.has(program.platform)) {
      return false;
    }

    if (selectedBountyTypes.size > 0 && !selectedBountyTypes.has(program.bountyType)) {
      return false;
    }

    if (filters.minBounty > 0) {
      if (program.bountyType !== "Cash" || program.bountyMaxUsd < filters.minBounty) {
        return false;
      }
    }

    if (!matchesScopeTypes(program, filters.scopeTypes)) {
      return false;
    }

    if (selectedAssetTypes.size > 0 && !program.assetTypes.some((asset) => selectedAssetTypes.has(asset))) {
      return false;
    }

    if (filters.indiaOnly && !program.isIndiaRelevant) {
      return false;
    }

    if (filters.activelyHuntedOnly && !program.isActivelyHunted) {
      return false;
    }

    if (!matchesPreset(program, filters.preset, generatedAt)) {
      return false;
    }

    if (!query) {
      return true;
    }

    const haystack = `${program.name} ${program.description} ${program.platform}`.toLowerCase();
    return haystack.includes(query);
  });

  return sortPrograms(results, filters.sortBy);
}
