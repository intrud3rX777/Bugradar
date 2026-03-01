export type BountyType = "Cash" | "Points" | "Swag" | "No bounty";
export type ProgramCategory = "VDP" | "BBP" | "Unknown";

export type AssetType = "Web" | "API" | "Mobile" | "Cloud" | "IoT" | "Source code";

export interface ScopeItem {
  target: string;
  type: string;
  assetType: AssetType;
  authRequired: boolean;
  notes: string;
}

export interface ScopeOutItem {
  target: string;
  reason: string;
}

export interface ScopeParsed {
  wildcardDomains: string[];
  exactDomains: string[];
  urlPaths: string[];
  authFlows: string[];
}

export interface PriorityBreakdown {
  scopeBreadth: number;
  wildcardCoverage: number;
  assetValue: number;
  bountyRange: number;
  freshness: number;
  recentScopeExpansion: number;
}

export interface Program {
  id: string;
  programId: string;
  sourceId: string;
  platform: string;
  name: string;
  description: string;
  url: string;
  bountyType: BountyType;
  programCategory: ProgramCategory;
  bountyMinUsd: number;
  bountyMaxUsd: number;
  bountyRange: string;
  scopeSummary: string;
  scope: {
    in: ScopeItem[];
    out: ScopeOutItem[];
  };
  scopeParsed: ScopeParsed;
  assetTypes: AssetType[];
  isIndiaRelevant: boolean;
  indiaSignals: string[];
  hqCountry: string;
  rules: string[];
  exclusions: string[];
  createdAt: string;
  lastUpdated: string;
  priorityScore: number;
  priorityBreakdown: PriorityBreakdown;
  submissionCount: number | null;
  submissionsLast7d: number | null;
  lastSubmissionAt: string | null;
  isActivelyHunted: boolean;
  activitySignals: string[];
  metadata: {
    regions: string[];
    recentScopeExpansion: boolean;
  };
}

export interface ProgramsPayload {
  generatedAt: string;
  version: string;
  totalPrograms: number;
  programs: Program[];
}

export interface ChangeItem {
  timestamp: string;
  type:
    | "new_program"
    | "program_removed"
    | "scope_added"
    | "scope_removed"
    | "bounty_changed"
    | "asset_type_changed";
  programId: string;
  programName: string;
  platform: string;
  before?: unknown;
  after?: unknown;
  details: Record<string, unknown>;
}

export interface ChangesPayload {
  generatedAt: string;
  comparedAgainst?: string;
  summary: {
    totalChanges: number;
    newPrograms: number;
    removedPrograms: number;
    scopeAdditions: number;
    scopeRemovals: number;
    bountyChanges: number;
    assetTypeChanges: number;
  };
  items: ChangeItem[];
}

export type ActivityType =
  | "new_program"
  | "program_removed"
  | "scope_added"
  | "scope_removed"
  | "bounty_changed"
  | "asset_type_changed"
  | "recent_submission"
  | "high_submission_volume"
  | "program_updated";

export interface ActivityItem {
  id: string;
  timestamp: string;
  type: ActivityType;
  platform: string;
  programId: string;
  programName: string;
  programUrl: string;
  summary: string;
}

export interface ActivityPayload {
  generatedAt: string;
  totalEvents: number;
  items: ActivityItem[];
  byType: Array<StatsBucket & { type: string }>;
  byPlatform: Array<StatsBucket & { platform: string }>;
}

export type HacktivitySource =
  | "hackerone_hacktivity"
  | "bugcrowd_crowdstream"
  | "platform_signal";

export interface HacktivityItem {
  id: string;
  platform: string;
  source: HacktivitySource;
  timestamp: string;
  programId: string | null;
  programName: string;
  programUrl: string;
  reportTitle: string;
  summary: string;
  severity: string | null;
  target: string | null;
  bountyAmountUsd: number | null;
  bountyLabel: string | null;
  reporter: string | null;
  state: string | null;
  disclosed: boolean | null;
  link: string;
}

export interface HacktivityPayload {
  generatedAt: string;
  totalItems: number;
  items: HacktivityItem[];
  byPlatform: Array<StatsBucket & { platform: string }>;
  bySource: Array<StatsBucket & { source: HacktivitySource | string }>;
}

export type LatestUpdateType = "new_program" | "scope_update";

export interface LatestUpdateItem {
  id: string;
  type: LatestUpdateType;
  timestamp: string;
  programId: string | null;
  programName: string;
  platform: string;
  programUrl: string;
  summary: string;
  program: {
    category: ProgramCategory | string;
    bountyType: BountyType | string | null;
    bountyRange: string | null;
    priorityScore: number | null;
    scopeSummary: string | null;
    assetTypes: string[];
    isIndiaRelevant: boolean;
  };
  scopeChange: {
    direction: "added" | "removed";
    targets: string[];
    count: number;
  } | null;
}

export interface LatestUpdatesPayload {
  generatedAt: string;
  windowDays: number;
  windowStart: string;
  summary: {
    totalItems: number;
    newPrograms: number;
    scopeUpdates: number;
    programsWithScopeUpdates: number;
  };
  items: LatestUpdateItem[];
  byPlatform: Array<StatsBucket & { platform: string }>;
  byType: Array<StatsBucket & { type: LatestUpdateType | string }>;
}

export interface StatsBucket {
  count: number;
}

export interface StatsPayload {
  generatedAt: string;
  totals: {
    programs: number;
    indiaRelevant: number;
    cashPrograms: number;
    avgPriorityScore: number;
  };
  byPlatform: Array<StatsBucket & { platform: string }>;
  byAssetType: Array<StatsBucket & { assetType: string }>;
  byBountyType: Array<StatsBucket & { bountyType: string }>;
  topPriorityPrograms: Array<{
    id: string;
    name: string;
    platform: string;
    priorityScore: number;
  }>;
}
