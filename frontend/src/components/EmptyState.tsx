import type { ReactNode } from "react";
import "./EmptyState.css";

// Ports `jobfinder/ui/widget.py`'s `_build_empty` / `_empty_match_copy` /
// the "Run a scan now" CTA (`_build_scanning_empty` is T24+ territory —
// the live spinner frame — this just covers the static empty copy).
export function EmptyState({
  title,
  subtitle,
  ctaLabel,
  onCta,
}: {
  title: string;
  subtitle: string;
  ctaLabel?: string;
  onCta?: () => void;
}): ReactNode {
  return (
    <div className="empty-state" data-testid="empty-state">
      <p className="empty-state-title">{title}</p>
      <p className="empty-state-sub">{subtitle}</p>
      {ctaLabel && onCta ? (
        <button type="button" className="empty-state-cta" onClick={onCta}>
          {ctaLabel}
        </button>
      ) : null}
    </div>
  );
}
