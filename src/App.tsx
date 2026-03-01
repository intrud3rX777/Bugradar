import { useEffect, useMemo, useState } from "react";
import {
  PRESET_FILTERS,
  SCOPE_FILTERS,
  buildDefaultFilters,
  buildFilterOptions,
  filterPrograms,
  getPlatformCounts,
  type FilterState,
  type PresetId,
  type ScopeFilterKey,
} from "./filters";
import type {
  BountyType,
  ChangeItem,
  ChangesPayload,
  HacktivityItem,
  HacktivityPayload,
  LatestUpdateItem,
  LatestUpdatesPayload,
  Program,
  ProgramsPayload,
  StatsPayload,
} from "./types";
import {
  formatAge,
  formatDate,
  formatUsd,
  getRouteFromHash,
  toProgramHash,
  toTabHash,
  type AppTab,
  type HashRoute,
} from "./utils";

const THEME_STORAGE_KEY = "bug-radar-theme";

type Theme = "light" | "dark";

function getInitialTheme(): Theme {
  const saved = window.localStorage.getItem(THEME_STORAGE_KEY);
  if (saved === "light" || saved === "dark") {
    return saved;
  }
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function toggleString(list: string[], value: string): string[] {
  if (list.includes(value)) {
    return list.filter((item) => item !== value);
  }
  return [...list, value];
}

function toggleScope(list: ScopeFilterKey[], value: ScopeFilterKey): ScopeFilterKey[] {
  if (list.includes(value)) {
    return list.filter((item) => item !== value);
  }
  return [...list, value];
}

function toggleBounty(list: BountyType[], value: BountyType): BountyType[] {
  if (list.includes(value)) {
    return list.filter((item) => item !== value);
  }
  return [...list, value];
}

async function fetchJson<T>(path: string): Promise<T> {
  const response = await fetch(path, { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`Failed to fetch ${path}: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

function changeLabel(changeType: ChangeItem["type"]): string {
  switch (changeType) {
    case "new_program":
      return "New program";
    case "program_removed":
      return "Program removed";
    case "scope_added":
      return "Scope added";
    case "scope_removed":
      return "Scope removed";
    case "bounty_changed":
      return "Bounty changed";
    case "asset_type_changed":
      return "Asset type changed";
    default:
      return changeType;
  }
}

function hacktivitySourceLabel(source: HacktivityItem["source"] | string): string {
  switch (source) {
    case "hackerone_hacktivity":
      return "HackerOne Hacktivity";
    case "bugcrowd_crowdstream":
      return "Bugcrowd Crowdstream";
    case "platform_signal":
      return "Platform activity signal";
    default:
      return source;
  }
}

function latestUpdateTypeLabel(item: LatestUpdateItem): string {
  if (item.type === "new_program") {
    return "New Program";
  }
  const direction = item.scopeChange?.direction;
  if (direction === "added") {
    return "Scope Added";
  }
  if (direction === "removed") {
    return "Scope Removed";
  }
  return "Scope Updated";
}

function renderChangePayload(payload: unknown): string {
  if (payload === null || payload === undefined) {
    return "-";
  }
  if (typeof payload === "string") {
    return payload;
  }
  if (Array.isArray(payload)) {
    return payload.join(", ");
  }
  if (typeof payload === "object") {
    return JSON.stringify(payload);
  }
  return String(payload);
}

interface DetailProps {
  generatedAt: string;
  program: Program;
  changes: ChangeItem[];
  onBack: () => void;
}

function ProgramDetail({ generatedAt, program, changes, onBack }: DetailProps): JSX.Element {
  const visibleScopeItems = program.scope.in.filter((item) => item.target !== "public-program-scope");
  const hasScopeListingPlaceholder = visibleScopeItems.length === 0 && program.scope.in.length > 0;

  return (
    <div className="detail-shell">
      <header className="detail-header">
        <button className="ghost-button" type="button" onClick={onBack}>
          Back
        </button>
        <div className="detail-title">
          <h1>{program.name}</h1>
          <p>
            {program.platform} | Last updated {formatDate(program.lastUpdated)} ({formatAge(program.lastUpdated, generatedAt)})
          </p>
        </div>
        <a className="primary-link" href={program.url} target="_blank" rel="noreferrer">
          Open Program
        </a>
      </header>

      <section className="detail-grid">
        <article className="panel">
          <h2>Overview</h2>
          <p>{program.description}</p>
          <div className="token-row">
            <span className="token">{program.bountyType}</span>
            <span className="token">{program.bountyRange}</span>
            {program.isIndiaRelevant && <span className="token token-india">India Relevant</span>}
            {program.isActivelyHunted && <span className="token token-active">Actively hunted</span>}
            <span className="token">
              Submissions: {program.submissionCount !== null ? program.submissionCount.toLocaleString() : "N/A"}
            </span>
            <span className="token">
              7d: {program.submissionsLast7d !== null ? program.submissionsLast7d.toLocaleString() : "N/A"}
            </span>
          </div>
        </article>

        <article className="panel">
          <h2>In Scope Assets ({visibleScopeItems.length})</h2>
          {hasScopeListingPlaceholder ? (
            <p className="muted">Scope details are not publicly listed by the source platform.</p>
          ) : (
            <div className="scope-list">
              {visibleScopeItems.map((item) => (
                <div key={`${item.target}-${item.type}`} className="scope-item">
                  <code>{item.target}</code>
                  <span>{item.type}</span>
                  <span>{item.assetType}</span>
                </div>
              ))}
            </div>
          )}
        </article>

        <article className="panel">
          <h2>Out of Scope ({program.scope.out.length})</h2>
          <div className="scope-list">
            {program.scope.out.map((item) => (
              <div key={`${item.target}-${item.reason}`} className="scope-item">
                <code>{item.target}</code>
                <span>{item.reason}</span>
              </div>
            ))}
          </div>
        </article>

        <article className="panel">
          <h2>Change History</h2>
          {changes.length === 0 ? (
            <p className="muted">No tracked changes yet for this program.</p>
          ) : (
            <div className="changes-list">
              {changes.map((change, index) => (
                <div className="change-item" key={`${change.type}-${index}`}>
                  <header>
                    <strong>{changeLabel(change.type)}</strong>
                    <span>{formatDate(change.timestamp)}</span>
                  </header>
                  <p>
                    <span>Before: {renderChangePayload(change.before)}</span>
                    <span>After: {renderChangePayload(change.after)}</span>
                  </p>
                </div>
              ))}
            </div>
          )}
        </article>
      </section>
    </div>
  );
}

interface NavProps {
  activeTab: AppTab;
  hacktivityCount: number;
  updatesCount: number;
  theme: Theme;
  onChangeTab: (tab: AppTab) => void;
  onToggleTheme: () => void;
}

function compactCount(value: number): string {
  if (value >= 1000) {
    const rounded = Math.floor(value / 100) / 10;
    return `${rounded}k`;
  }
  return String(value);
}

function SideNav({ activeTab, hacktivityCount, updatesCount, theme, onChangeTab, onToggleTheme }: NavProps): JSX.Element {
  const isDark = theme === "dark";

  return (
    <aside className="main-nav">
      <div className="nav-brand" title="Bug Radar">
        <div className="nav-logo" aria-hidden="true">
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="8.5" />
            <circle cx="12" cy="12" r="3.2" />
            <path d="M12 3.5v2.2M12 18.3v2.2M3.5 12h2.2M18.3 12h2.2" />
            <path d="M5.7 5.7l1.6 1.6M16.7 16.7l1.6 1.6M5.7 18.3l1.6-1.6M16.7 7.3l1.6-1.6" />
          </svg>
        </div>
        <span className="sr-only">Bug Radar</span>
      </div>

      <nav className="nav-links">
        <button
          type="button"
          className={activeTab === "dashboard" ? "nav-link active" : "nav-link"}
          onClick={() => onChangeTab("dashboard")}
          aria-label="Dashboard"
          title="Dashboard"
        >
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <rect x="4" y="4" width="7" height="7" rx="1.5" />
            <rect x="13" y="4" width="7" height="4" rx="1.2" />
            <rect x="13" y="10" width="7" height="10" rx="1.5" />
            <rect x="4" y="13" width="7" height="7" rx="1.5" />
          </svg>
          <span className="sr-only">Dashboard</span>
        </button>
        <button
          type="button"
          className={activeTab === "hacktivity" ? "nav-link active" : "nav-link"}
          onClick={() => onChangeTab("hacktivity")}
          aria-label={`Hacktivity ${hacktivityCount.toLocaleString()} items`}
          title="Hacktivity"
        >
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M3 12h4l2.1-4.6 4 9.2 2.1-4.6H21" />
            <path d="M12 4v2M12 18v2" />
          </svg>
          <span className="nav-badge">{compactCount(hacktivityCount)}</span>
          <span className="sr-only">Hacktivity</span>
        </button>
        <button
          type="button"
          className={activeTab === "updates" ? "nav-link active" : "nav-link"}
          onClick={() => onChangeTab("updates")}
          aria-label={`Latest updates ${updatesCount.toLocaleString()} items`}
          title="Latest Updates"
        >
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M4 6h16M4 12h10M4 18h13" />
            <circle cx="18" cy="12" r="1.6" />
          </svg>
          <span className="nav-badge">{compactCount(updatesCount)}</span>
          <span className="sr-only">Latest Updates</span>
        </button>
      </nav>

      <button
        type="button"
        className="ghost-button nav-theme"
        onClick={onToggleTheme}
        aria-label={isDark ? "Switch to light mode" : "Switch to dark mode"}
        title={isDark ? "Light Mode" : "Dark Mode"}
      >
        {isDark ? (
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <circle cx="12" cy="12" r="4.2" />
            <path d="M12 2.6v2.2M12 19.2v2.2M4.8 12H2.6M21.4 12h-2.2M6.8 6.8 5.2 5.2M18.8 18.8l-1.6-1.6M6.8 17.2l-1.6 1.6M18.8 5.2l-1.6 1.6" />
          </svg>
        ) : (
          <svg viewBox="0 0 24 24" className="nav-icon" fill="none" stroke="currentColor" strokeWidth="1.8">
            <path d="M20 14.2A8.2 8.2 0 1 1 9.8 4a6.4 6.4 0 1 0 10.2 10.2z" />
          </svg>
        )}
        <span className="sr-only">{isDark ? "Light Mode" : "Dark Mode"}</span>
      </button>
    </aside>
  );
}

function App(): JSX.Element {
  const [theme, setTheme] = useState<Theme>(() => getInitialTheme());
  const [programPayload, setProgramPayload] = useState<ProgramsPayload | null>(null);
  const [changesPayload, setChangesPayload] = useState<ChangesPayload | null>(null);
  const [hacktivityPayload, setHacktivityPayload] = useState<HacktivityPayload | null>(null);
  const [latestUpdatesPayload, setLatestUpdatesPayload] = useState<LatestUpdatesPayload | null>(null);
  const [statsPayload, setStatsPayload] = useState<StatsPayload | null>(null);
  const [filters, setFilters] = useState<FilterState | null>(null);
  const [route, setRoute] = useState<HashRoute>(() => getRouteFromHash(window.location.hash));
  const [hacktivityQuery, setHacktivityQuery] = useState<string>("");
  const [hacktivityPlatforms, setHacktivityPlatforms] = useState<string[]>([]);
  const [updatesQuery, setUpdatesQuery] = useState<string>("");
  const [updatesPlatforms, setUpdatesPlatforms] = useState<string[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  }, [theme]);

  useEffect(() => {
    const onHashChange = () => {
      setRoute(getRouteFromHash(window.location.hash));
    };
    window.addEventListener("hashchange", onHashChange);
    return () => window.removeEventListener("hashchange", onHashChange);
  }, []);

  useEffect(() => {
    const base = import.meta.env.BASE_URL;
    const load = async () => {
      try {
        setLoading(true);
        setError(null);
        const refreshToken = Date.now();

        const [programs, changes, hacktivity, latestUpdates, stats] = await Promise.all([
          fetchJson<ProgramsPayload>(`${base}data/programs.json?v=${refreshToken}`),
          fetchJson<ChangesPayload>(`${base}data/changes.json?v=${refreshToken}`),
          fetchJson<HacktivityPayload>(`${base}data/hacktivity.json?v=${refreshToken}`),
          fetchJson<LatestUpdatesPayload>(`${base}data/latest_updates.json?v=${refreshToken}`),
          fetchJson<StatsPayload>(`${base}data/stats.json?v=${refreshToken}`),
        ]);

        setProgramPayload(programs);
        setChangesPayload(changes);
        setHacktivityPayload(hacktivity);
        setLatestUpdatesPayload(latestUpdates);
        setStatsPayload(stats);

        const options = buildFilterOptions(programs.programs);
        setFilters(buildDefaultFilters(options));

        const platforms = Array.from(
          new Set([
            ...hacktivity.byPlatform.map((item) => item.platform),
            ...hacktivity.items.map((item) => item.platform),
          ])
        ).sort((left, right) => left.localeCompare(right));
        setHacktivityPlatforms(platforms);

        const updatePlatforms = Array.from(
          new Set([
            ...latestUpdates.byPlatform.map((item) => item.platform),
            ...latestUpdates.items.map((item) => item.platform),
          ])
        ).sort((left, right) => left.localeCompare(right));
        setUpdatesPlatforms(updatePlatforms);
      } catch (loadError) {
        const message = loadError instanceof Error ? loadError.message : "Unexpected error";
        setError(message);
      } finally {
        setLoading(false);
      }
    };

    void load();
  }, []);

  const programs = programPayload?.programs ?? [];
  const generatedAt = programPayload?.generatedAt ?? new Date().toISOString();
  const platformCounts = useMemo(() => getPlatformCounts(programs), [programs]);
  const filterOptions = useMemo(() => buildFilterOptions(programs), [programs]);

  const filteredPrograms = useMemo(() => {
    if (!filters || !programPayload) {
      return [];
    }
    return filterPrograms(programPayload.programs, filters, generatedAt);
  }, [filters, generatedAt, programPayload]);

  const selectedProgram = useMemo(() => {
    if (!route.programId) {
      return null;
    }
    return programs.find((program) => program.id === route.programId) ?? null;
  }, [programs, route.programId]);

  const selectedProgramChanges = useMemo(() => {
    if (!route.programId || !changesPayload) {
      return [];
    }
    return changesPayload.items.filter((item) => item.programId === route.programId);
  }, [changesPayload, route.programId]);

  const availableProgramIds = useMemo(() => new Set(programs.map((program) => program.id)), [programs]);

  const hacktivityPlatformOptions = useMemo(() => {
    if (!hacktivityPayload) {
      return [];
    }
    return Array.from(
      new Set([
        ...hacktivityPayload.byPlatform.map((item) => item.platform),
        ...hacktivityPayload.items.map((item) => item.platform),
      ])
    ).sort((left, right) => left.localeCompare(right));
  }, [hacktivityPayload]);

  const hacktivityPlatformCounts = useMemo(() => {
    if (!hacktivityPayload) {
      return {} as Record<string, number>;
    }
    return hacktivityPayload.byPlatform.reduce<Record<string, number>>((acc, item) => {
      acc[item.platform] = item.count;
      return acc;
    }, {});
  }, [hacktivityPayload]);

  const filteredHacktivityItems = useMemo(() => {
    if (!hacktivityPayload) {
      return [];
    }
    const query = hacktivityQuery.trim().toLowerCase();
    const selectedPlatforms = new Set(hacktivityPlatforms);

    return hacktivityPayload.items.filter((item) => {
      if (selectedPlatforms.size > 0 && !selectedPlatforms.has(item.platform)) {
        return false;
      }
      if (!query) {
        return true;
      }
      const haystack = `${item.platform} ${item.programName} ${item.reportTitle} ${item.summary} ${item.target ?? ""}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [hacktivityPayload, hacktivityPlatforms, hacktivityQuery]);

  const updatesPlatformOptions = useMemo(() => {
    if (!latestUpdatesPayload) {
      return [];
    }
    return Array.from(
      new Set([
        ...latestUpdatesPayload.byPlatform.map((item) => item.platform),
        ...latestUpdatesPayload.items.map((item) => item.platform),
      ])
    ).sort((left, right) => left.localeCompare(right));
  }, [latestUpdatesPayload]);

  const updatesPlatformCounts = useMemo(() => {
    if (!latestUpdatesPayload) {
      return {} as Record<string, number>;
    }
    return latestUpdatesPayload.byPlatform.reduce<Record<string, number>>((acc, item) => {
      acc[item.platform] = item.count;
      return acc;
    }, {});
  }, [latestUpdatesPayload]);

  const filteredLatestUpdates = useMemo(() => {
    if (!latestUpdatesPayload) {
      return [];
    }
    const query = updatesQuery.trim().toLowerCase();
    const selectedPlatforms = new Set(updatesPlatforms);

    return latestUpdatesPayload.items.filter((item) => {
      if (selectedPlatforms.size > 0 && !selectedPlatforms.has(item.platform)) {
        return false;
      }
      if (!query) {
        return true;
      }
      const targetText = item.scopeChange?.targets.join(" ") ?? "";
      const haystack =
        `${item.platform} ${item.programName} ${item.summary} ${targetText}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [latestUpdatesPayload, updatesPlatforms, updatesQuery]);

  if (loading) {
    return (
      <main className="screen-state">
        <p>Loading bug bounty dataset...</p>
      </main>
    );
  }

  if (
    error ||
    !filters ||
    !programPayload ||
    !changesPayload ||
    !hacktivityPayload ||
    !latestUpdatesPayload ||
    !statsPayload
  ) {
    return (
      <main className="screen-state">
        <p>Unable to load dashboard data.</p>
        {error && <code>{error}</code>}
      </main>
    );
  }

  const onTabChange = (tab: AppTab) => {
    window.location.hash = toTabHash(tab);
  };

  const shellNav = (
    <SideNav
      activeTab={route.tab}
      hacktivityCount={hacktivityPayload.totalItems}
      updatesCount={latestUpdatesPayload.summary.totalItems}
      theme={theme}
      onChangeTab={onTabChange}
      onToggleTheme={() => setTheme((current) => (current === "dark" ? "light" : "dark"))}
    />
  );

  if (route.programId) {
    if (!selectedProgram) {
      return (
        <div className="workspace-shell">
          {shellNav}
          <main className="main-content">
            <main className="screen-state">
              <p>Program not found.</p>
              <button className="ghost-button" type="button" onClick={() => (window.location.hash = toTabHash(route.tab))}>
                Back
              </button>
            </main>
          </main>
        </div>
      );
    }

    return (
      <div className="workspace-shell">
        {shellNav}
        <main className="main-content">
          <ProgramDetail
            generatedAt={generatedAt}
            program={selectedProgram}
            changes={selectedProgramChanges}
            onBack={() => {
              window.location.hash = toTabHash(route.tab);
            }}
          />
        </main>
      </div>
    );
  }

  return (
    <div className="workspace-shell">
      {shellNav}
      <main className="main-content">
        {route.tab === "dashboard" ? (
          <div className="app-shell">
            <header className="topbar">
              <div>
                <p className="eyebrow">Bug Radar</p>
                <h1>Dashboard</h1>
                <p className="muted">
                  Unified public programs across HackerOne, Bugcrowd, Intigriti, YesWeHack, and OpenBugBounty.
                </p>
              </div>
            </header>

            <section className="stats-grid">
              <article className="stat-card">
                <span>Total Programs</span>
                <strong>{statsPayload.totals.programs}</strong>
              </article>
              <article className="stat-card">
                <span>India Relevant</span>
                <strong>{statsPayload.totals.indiaRelevant}</strong>
              </article>
              <article className="stat-card">
                <span>Cash Programs</span>
                <strong>{statsPayload.totals.cashPrograms}</strong>
              </article>
              <article className="stat-card">
                <span>Average Priority</span>
                <strong>{statsPayload.totals.avgPriorityScore}</strong>
              </article>
            </section>

            <section className="layout-grid">
              <aside className="filter-panel">
                <section className="panel-block">
                  <label htmlFor="search">Search programs</label>
                  <input
                    id="search"
                    type="search"
                    placeholder="Name, platform, description"
                    value={filters.query}
                    onChange={(event) => setFilters((current) => (current ? { ...current, query: event.target.value } : current))}
                  />
                </section>

                <section className="panel-block">
                  <div className="panel-header">
                    <h2>Preset Filters</h2>
                    <button type="button" onClick={() => setFilters(buildDefaultFilters(filterOptions))}>
                      Reset
                    </button>
                  </div>
                  <div className="preset-list">
                    {PRESET_FILTERS.map((preset) => (
                      <button
                        key={preset.id}
                        type="button"
                        className={filters.preset === preset.id ? "preset active" : "preset"}
                        title={preset.description}
                        onClick={() =>
                          setFilters((current) =>
                            current
                              ? { ...current, preset: current.preset === preset.id ? ("none" as PresetId) : preset.id }
                              : current
                          )
                        }
                      >
                        {preset.label}
                      </button>
                    ))}
                  </div>
                </section>

                <section className="panel-block">
                  <h2>Platform</h2>
                  <div className="check-list">
                    {filterOptions.platforms.map((platform) => (
                      <label key={platform}>
                        <input
                          type="checkbox"
                          checked={filters.platforms.includes(platform)}
                          onChange={() =>
                            setFilters((current) =>
                              current
                                ? {
                                    ...current,
                                    platforms: toggleString(current.platforms, platform),
                                  }
                                : current
                            )
                          }
                        />
                        <span>{platform}</span>
                        <small>{platformCounts[platform] ?? 0}</small>
                      </label>
                    ))}
                  </div>
                </section>

                <section className="panel-block">
                  <h2>Bounty Type</h2>
                  <div className="check-list">
                    {filterOptions.bountyTypes.map((bountyType) => (
                      <label key={bountyType}>
                        <input
                          type="checkbox"
                          checked={filters.bountyTypes.includes(bountyType)}
                          onChange={() =>
                            setFilters((current) =>
                              current
                                ? {
                                    ...current,
                                    bountyTypes: toggleBounty(current.bountyTypes, bountyType),
                                  }
                                : current
                            )
                          }
                        />
                        <span>{bountyType}</span>
                      </label>
                    ))}
                  </div>

                  <label htmlFor="min-bounty" className="range-label">
                    Minimum bounty: {formatUsd(filters.minBounty)}
                  </label>
                  <input
                    id="min-bounty"
                    type="range"
                    min={0}
                    max={20000}
                    step={250}
                    value={filters.minBounty}
                    onChange={(event) =>
                      setFilters((current) => (current ? { ...current, minBounty: Number(event.target.value) } : current))
                    }
                  />
                </section>

                <section className="panel-block">
                  <h2>Scope Type</h2>
                  <div className="check-list">
                    {SCOPE_FILTERS.map((scopeFilter) => (
                      <label key={scopeFilter.key}>
                        <input
                          type="checkbox"
                          checked={filters.scopeTypes.includes(scopeFilter.key)}
                          onChange={() =>
                            setFilters((current) =>
                              current
                                ? {
                                    ...current,
                                    scopeTypes: toggleScope(current.scopeTypes, scopeFilter.key),
                                  }
                                : current
                            )
                          }
                        />
                        <span>{scopeFilter.label}</span>
                      </label>
                    ))}
                  </div>
                </section>

                <section className="panel-block">
                  <h2>Asset Type</h2>
                  <div className="check-list">
                    {filterOptions.assetTypes.map((assetType) => (
                      <label key={assetType}>
                        <input
                          type="checkbox"
                          checked={filters.assetTypes.includes(assetType)}
                          onChange={() =>
                            setFilters((current) =>
                              current
                                ? {
                                    ...current,
                                    assetTypes: toggleString(current.assetTypes, assetType),
                                  }
                                : current
                            )
                          }
                        />
                        <span>{assetType}</span>
                      </label>
                    ))}
                  </div>
                </section>

                <section className="panel-block">
                  <label className="single-toggle">
                    <input
                      type="checkbox"
                      checked={filters.indiaOnly}
                      onChange={(event) =>
                        setFilters((current) => (current ? { ...current, indiaOnly: event.target.checked } : current))
                      }
                    />
                    <span>India Only</span>
                  </label>
                </section>

                <section className="panel-block">
                  <label className="single-toggle">
                    <input
                      type="checkbox"
                      checked={filters.activelyHuntedOnly}
                      onChange={(event) =>
                        setFilters((current) =>
                          current ? { ...current, activelyHuntedOnly: event.target.checked } : current
                        )
                      }
                    />
                    <span>Actively Hunted</span>
                  </label>
                  <p className="muted">Based on submission activity published by supported platforms.</p>
                </section>
              </aside>

              <section className="results-panel">
                <header className="results-header">
                  <div>
                    <h2>Programs</h2>
                    <p>
                      Showing {filteredPrograms.length} of {programs.length}
                    </p>
                  </div>
                  <label>
                    Sort
                    <select
                      value={filters.sortBy}
                      onChange={(event) =>
                        setFilters((current) =>
                          current ? { ...current, sortBy: event.target.value as FilterState["sortBy"] } : current
                        )
                      }
                    >
                      <option value="priority">Priority</option>
                      <option value="maxBounty">Max bounty</option>
                      <option value="updated">Recently updated</option>
                      <option value="submissions">Submissions</option>
                    </select>
                  </label>
                </header>

                {filteredPrograms.length === 0 ? (
                  <div className="empty-state">
                    <p>No programs match the current filters.</p>
                    <button type="button" onClick={() => setFilters(buildDefaultFilters(filterOptions))}>
                      Clear filters
                    </button>
                  </div>
                ) : (
                  <div className="program-grid">
                    {filteredPrograms.map((program, index) => (
                      <article
                        className="program-card"
                        key={program.id}
                        style={{ animationDelay: `${Math.min(index * 45, 360)}ms` }}
                      >
                        <header>
                          <div>
                            <h3>{program.name}</h3>
                            <p>{program.platform}</p>
                          </div>
                          <span className="priority-badge">{program.priorityScore}</span>
                        </header>

                        <p className="description">{program.description}</p>

                        <div className="meta-row">
                          <span>{program.bountyType}</span>
                          <strong>{program.bountyType === "Cash" ? formatUsd(program.bountyMaxUsd) : program.bountyRange}</strong>
                        </div>

                        <p className="submission-summary">
                          Submissions: {program.submissionCount !== null ? program.submissionCount.toLocaleString() : "N/A"} | 7d:{" "}
                          {program.submissionsLast7d !== null ? program.submissionsLast7d.toLocaleString() : "N/A"}
                        </p>

                        <p className="scope-summary">{program.scopeSummary}</p>

                        <div className="chips">
                          <span>{program.programCategory}</span>
                          {program.assetTypes.map((assetType) => (
                            <span key={`${program.id}-${assetType}`}>{assetType}</span>
                          ))}
                          {program.isActivelyHunted && <span className="chip-active">Actively hunted</span>}
                          {program.isIndiaRelevant && <span className="chip-india">India</span>}
                        </div>

                        <footer>
                          <small>{formatAge(program.lastUpdated, generatedAt)}</small>
                          <button type="button" onClick={() => (window.location.hash = toProgramHash(program.id, "dashboard"))}>
                            View details
                          </button>
                        </footer>
                      </article>
                    ))}
                  </div>
                )}
              </section>
            </section>
          </div>
        ) : route.tab === "updates" ? (
          <div className="app-shell">
            <header className="topbar">
              <div>
                <p className="eyebrow">Bug Radar</p>
                <h1>Latest Updates (7d)</h1>
                <p className="muted">
                  Recently added programs and scope changes detected in the last {latestUpdatesPayload.windowDays} days.
                </p>
              </div>
            </header>

            <section className="updates-toolbar">
              <div className="panel-block">
                <label htmlFor="updates-search">Search updates</label>
                <input
                  id="updates-search"
                  type="search"
                  placeholder="Program, platform, summary, targets"
                  value={updatesQuery}
                  onChange={(event) => setUpdatesQuery(event.target.value)}
                />
              </div>

              <div className="panel-block">
                <div className="panel-header">
                  <h2>Platforms</h2>
                  <button type="button" onClick={() => setUpdatesPlatforms([...updatesPlatformOptions])}>
                    Reset
                  </button>
                </div>
                <div className="preset-list">
                  {updatesPlatformOptions.map((platform) => (
                    <button
                      key={platform}
                      type="button"
                      className={updatesPlatforms.includes(platform) ? "preset active" : "preset"}
                      onClick={() => setUpdatesPlatforms((current) => toggleString(current, platform))}
                    >
                      {platform} ({updatesPlatformCounts[platform] ?? 0})
                    </button>
                  ))}
                </div>
              </div>
            </section>

            <section className="updates-summary-row">
              <article className="stat-card">
                <span>Total Updates</span>
                <strong>{latestUpdatesPayload.summary.totalItems}</strong>
              </article>
              <article className="stat-card">
                <span>New Programs</span>
                <strong>{latestUpdatesPayload.summary.newPrograms}</strong>
              </article>
              <article className="stat-card">
                <span>Scope Updates</span>
                <strong>{latestUpdatesPayload.summary.scopeUpdates}</strong>
              </article>
              <article className="stat-card">
                <span>Programs with Scope Changes</span>
                <strong>{latestUpdatesPayload.summary.programsWithScopeUpdates}</strong>
              </article>
            </section>

            <section className="updates-list">
              {filteredLatestUpdates.length === 0 ? (
                <div className="empty-state">
                  <p>No updates match the current filters.</p>
                  <button
                    type="button"
                    onClick={() => {
                      setUpdatesQuery("");
                      setUpdatesPlatforms([...updatesPlatformOptions]);
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              ) : (
                filteredLatestUpdates.map((item) => {
                  const itemProgramId = item.programId;
                  const canOpenProgram = typeof itemProgramId === "string" && availableProgramIds.has(itemProgramId);
                  const scopeTargets = item.scopeChange?.targets ?? [];

                  return (
                    <article className="update-card" key={item.id}>
                      <header>
                        <div className="update-title">
                          <span className="token">{item.platform}</span>
                          <h3>{latestUpdateTypeLabel(item)}</h3>
                        </div>
                        <small>{formatAge(item.timestamp, generatedAt)}</small>
                      </header>

                      <p className="update-program">{item.programName}</p>
                      <p className="update-summary">{item.summary}</p>

                      <div className="chips">
                        <span>{item.program.category}</span>
                        {item.program.bountyRange && <span>{item.program.bountyRange}</span>}
                        {item.program.priorityScore !== null && item.program.priorityScore !== undefined && (
                          <span>Priority {item.program.priorityScore}</span>
                        )}
                        {item.program.isIndiaRelevant && <span className="chip-india">India</span>}
                        {scopeTargets.slice(0, 4).map((target) => (
                          <span key={`${item.id}-${target}`}>{target}</span>
                        ))}
                      </div>

                      <footer className="update-footer">
                        <small>{formatDate(item.timestamp)}</small>
                        <div className="update-actions">
                          {canOpenProgram && (
                            <button
                              type="button"
                              onClick={() => (window.location.hash = toProgramHash(itemProgramId, "updates"))}
                            >
                              View program
                            </button>
                          )}
                          {item.programUrl && (
                            <a href={item.programUrl} target="_blank" rel="noreferrer">
                              Open source
                            </a>
                          )}
                        </div>
                      </footer>
                    </article>
                  );
                })
              )}
            </section>
          </div>
        ) : (
          <div className="app-shell">
            <header className="topbar">
              <div>
                <p className="eyebrow">Bug Radar</p>
                <h1>Collective Hacktivity</h1>
                <p className="muted">
                  Unified activity stream inspired by HackerOne Hacktivity and Bugcrowd Crowdstream.
                </p>
              </div>
            </header>

            <section className="hacktivity-toolbar">
              <div className="panel-block">
                <label htmlFor="hacktivity-search">Search activity</label>
                <input
                  id="hacktivity-search"
                  type="search"
                  placeholder="Program, title, summary, target"
                  value={hacktivityQuery}
                  onChange={(event) => setHacktivityQuery(event.target.value)}
                />
              </div>

              <div className="panel-block">
                <div className="panel-header">
                  <h2>Platforms</h2>
                  <button type="button" onClick={() => setHacktivityPlatforms([...hacktivityPlatformOptions])}>
                    Reset
                  </button>
                </div>
                <div className="preset-list">
                  {hacktivityPlatformOptions.map((platform) => (
                    <button
                      key={platform}
                      type="button"
                      className={hacktivityPlatforms.includes(platform) ? "preset active" : "preset"}
                      onClick={() =>
                        setHacktivityPlatforms((current) => toggleString(current, platform))
                      }
                    >
                      {platform} ({hacktivityPlatformCounts[platform] ?? 0})
                    </button>
                  ))}
                </div>
              </div>
            </section>

            <section className="hacktivity-summary-row">
              <article className="stat-card">
                <span>Total Signals</span>
                <strong>{hacktivityPayload.totalItems}</strong>
              </article>
              {hacktivityPayload.bySource.map((sourceRow) => (
                <article className="stat-card" key={sourceRow.source}>
                  <span>{hacktivitySourceLabel(sourceRow.source)}</span>
                  <strong>{sourceRow.count}</strong>
                </article>
              ))}
            </section>

            <section className="hacktivity-list">
              {filteredHacktivityItems.length === 0 ? (
                <div className="empty-state">
                  <p>No hacktivity items match the current filters.</p>
                  <button
                    type="button"
                    onClick={() => {
                      setHacktivityQuery("");
                      setHacktivityPlatforms([...hacktivityPlatformOptions]);
                    }}
                  >
                    Clear filters
                  </button>
                </div>
              ) : (
                filteredHacktivityItems.map((item) => {
                  const itemProgramId = item.programId;
                  const canOpenProgram = typeof itemProgramId === "string" && availableProgramIds.has(itemProgramId);

                  return (
                    <article className="hacktivity-card" key={item.id}>
                      <header>
                        <div className="hacktivity-title">
                          <span className="token">{item.platform}</span>
                          <h3>{item.reportTitle}</h3>
                        </div>
                        <small>{formatAge(item.timestamp, generatedAt)}</small>
                      </header>

                      <p className="hacktivity-program">{item.programName}</p>
                      <p className="hacktivity-summary">{item.summary}</p>

                      <div className="chips">
                        {item.severity && <span>{item.severity}</span>}
                        {item.target && <span>{item.target}</span>}
                        {item.bountyLabel && <span>{item.bountyLabel}</span>}
                        {item.reporter && <span>@{item.reporter}</span>}
                        {item.state && <span>{item.state}</span>}
                        {item.disclosed === true && <span>Disclosed</span>}
                      </div>

                      <footer className="hacktivity-footer">
                        <small>{hacktivitySourceLabel(item.source)}</small>
                        <div className="hacktivity-actions">
                          {canOpenProgram && (
                            <button
                              type="button"
                              onClick={() => (window.location.hash = toProgramHash(itemProgramId, "hacktivity"))}
                            >
                              View program
                            </button>
                          )}
                          {item.link && (
                            <a href={item.link} target="_blank" rel="noreferrer">
                              Open source
                            </a>
                          )}
                        </div>
                      </footer>
                    </article>
                  );
                })
              )}
            </section>
          </div>
        )}
      </main>
    </div>
  );
}

export default App;
