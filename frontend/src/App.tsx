import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchNeedsOnboarding } from "./api/onboarding";
import { BoardView } from "./views/BoardView";
import { FillerBotView } from "./views/FillerBotView";
import { OnboardingView } from "./views/OnboardingView";
import { SettingsView } from "./views/SettingsView";
import { ResumeTab } from "./views/ResumeTab";
import { LogDrawer, type LogDrawerHandle } from "./components/LogDrawer";
import { Rail } from "./components/Rail";
import type { RailCounts, RailDestination } from "./components/railLogic";
import "./App.css";

// T60 — one navigation model (docs/DESIGN.md principle 4, the product spec
// bullet 4). `activeDestination` is now the single piece of state that
// decides what's on screen; it replaces the three independent pieces of
// UI state this file used to hold (`settingsOpen` driving an overlay,
// JobsView's own internal main-tab state, and LogDrawer's always-mounted
// fixed drawer) with the rail's one selection plus two small, genuinely
// separate toggles (the log drawer and the FillerBot modal, neither of
// which is a *destination* — see Rail.tsx's own docstring on why the
// filter panel isn't a third nav system either).
function App(): ReactNode {
  const [activeDestination, setActiveDestination] = useState<RailDestination>("board");
  const [moreOpen, setMoreOpen] = useState(false);

  // One entry point for FillerBot (T31), not one per job card — the Qt
  // app opened it from a single button on the floating widget
  // (`jobfinder/ui/widget.py:1381`), and the backend allows exactly one
  // session at a time, so a per-card button would offer N ways to hit the
  // same 409. Lives at the rail foot now (docs/DESIGN.md principle 4)
  // instead of the old floating trigger strip.
  const [fillerBotOpen, setFillerBotOpen] = useState(false);
  // T83: the job panel's "Fill it with FillerBot" action opens the same
  // one overlay above but seeded with the posting's URL — distinct from
  // the rail's foot button (`onOpenFillerBot` below), which opens it
  // blank since it carries no job context.
  const [fillerBotInitialUrl, setFillerBotInitialUrl] = useState<string | undefined>(undefined);
  const openFillerBotForJob = useCallback((url: string) => {
    setFillerBotInitialUrl(url);
    setFillerBotOpen(true);
  }, []);

  const logDrawerRef = useRef<LogDrawerHandle>(null);
  const toggleLog = useCallback(() => logDrawerRef.current?.toggle(), []);

  // Rail badge count (T81: the one Board destination's New-column size).
  // BoardView owns the real fetch (`reload()`) and reports back here —
  // this only goes stale while the user is looking at a non-board
  // destination (Résumé/Sources & keys), same way the old per-tab counts
  // only ever reflected the last `reload()` JobsView itself ran.
  const [counts, setCounts] = useState<RailCounts>({ board: 0 });

  // Web equivalent of main.py's `_needs_onboarding` gate (the Qt app's
  // own first-run check): `null` while the check is in flight, so the
  // main view never flashes behind the wizard on a fresh install.
  // `GET /api/onboarding/status` (jobfinder/web/onboarding.py) fails
  // *open* on error (treats it as "already configured") — the wizard
  // must never trap an already-working install behind a broken check.
  const [needsOnboarding, setNeedsOnboarding] = useState<boolean | null>(null);

  useEffect(() => {
    void fetchNeedsOnboarding().then(setNeedsOnboarding);
  }, []);

  const navigate = useCallback((destination: RailDestination) => {
    setActiveDestination(destination);
    setMoreOpen(false);
  }, []);

  if (needsOnboarding === null) {
    return null;
  }

  if (needsOnboarding) {
    return <OnboardingView onDone={() => setNeedsOnboarding(false)} />;
  }

  return (
    <div className="app-shell">
      <Rail
        active={activeDestination}
        onNavigate={navigate}
        counts={counts}
        moreOpen={moreOpen}
        onToggleMore={() => setMoreOpen((prev) => !prev)}
        onToggleLog={toggleLog}
        onOpenFillerBot={() => setFillerBotOpen(true)}
      />
      <main className="app-shell-main">
        {activeDestination === "board" ? (
          <BoardView onCountsChange={setCounts} onOpenFillerBot={openFillerBotForJob} />
        ) : null}
        {activeDestination === "resume" ? (
          <div className="shell-page">
            <ResumeTab />
          </div>
        ) : null}
        {activeDestination === "sources" ? <SettingsView /> : null}
      </main>
      {fillerBotOpen ? (
        <FillerBotView
          onClose={() => {
            setFillerBotOpen(false);
            setFillerBotInitialUrl(undefined);
          }}
          initialUrl={fillerBotInitialUrl}
        />
      ) : null}
      <LogDrawer ref={logDrawerRef} />
    </div>
  );
}

export default App;
