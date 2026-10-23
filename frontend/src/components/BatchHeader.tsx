import type { ReactNode } from "react";
import type { Batch, BatchableCard } from "./jobBatching";
import { batchHeaderText } from "./jobBatching";
import "./BatchHeader.css";

/** T58 — one header per scan batch, spanning the full grid width
 * regardless of density (`docs/DESIGN.md`: "Today 09:12 · 14 found ·
 * LinkedIn, Indeed"). Batches render newest first because
 * `GET /api/matches` already returns `get_recent_matches()` rows in that
 * order (`ORDER BY mh.matched_at DESC`) and `groupCardsByScan` is
 * order-preserving. */
export function BatchHeader<T extends BatchableCard>({ batch }: { batch: Batch<T> }): ReactNode {
  return <div className="batch-header">{batchHeaderText(batch)}</div>;
}
