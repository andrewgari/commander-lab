// review_document.v1 presentation contract. Validation remains the pipeline's job.
export type CardRef = { name: string; oracle_id: string };
export type Unavailable = { status: 'unavailable'; reason: string };
export type Finding = { id: string; axis: string; claim: string; evidence: { cards: CardRef[]; fact_keys: string[] }; severity: string; confidence: number };
export type Swap = { id: string; cut: CardRef; add: CardRef; resolves_finding_id: string; tradeoff: string; bracket_effect: 'none' | 'raises' | 'lowers'; budget_effect: string; alternative?: CardRef | null; owned: boolean; price?: number | null };
export type Curve = { buckets: { mv: number; count: number; label: string }[]; average_mv: number };
export type Pip = { color: string; pips: number; sources: number; recommended_sources: number };
export type Role = { role: string; count: number; target_min: number; target_max: number };
export type Cluster = { name: string; cards: CardRef[]; strength: number };
export interface ReviewDocument {
 schema_version: string;
 meta: { deck_id: string; deck_name: string; commanders: CardRef[]; generated_at: string; pipeline_version: string; prompt_versions: Record<string,string>; models: Record<string,string>; fact_pack_hash: string };
 verdict: { headline: string; summary: string; grade: string; confidence: number };
 deck_identity: { what_it_does: string; win_paths: string[]; success_criteria: string[]; intentional_quirks: string[] };
 scorecard: { axis: string; score: number; one_line: string; finding_ids: string[] }[];
 strengths: Finding[]; problems: Finding[];
 top_changes: { swap_ids: string[]; why_these_first: string };
 swaps: Swap[];
 cuts_to_consider: { card: CardRef; reason: string; evidence: string[] }[];
 bracket: { estimated: number; target: number | null; reasons: string[] };
 mana: { summary: string; issues: string[] };
 questions_for_owner: { id: string; question: string; why_asked: string }[];
 // Defensive display extension only; v1 schema does NOT yet admit these markers.
 charts: { curve: Curve | Unavailable; pips_vs_sources: Pip[] | Unavailable; roles: Role[] | Unavailable; synergy_clusters: Cluster[] | Unavailable };
 forge?: { status: string; reason?: string; games?: number; win_rate?: number | null; notes?: string[] };
 omitted: { kind: string; item: string; reason: string }[];
}
export const percent = (n: number) => `${Math.round(n * 100)}%`;
export const priceLabel = (n?: number | null) => n == null ? 'Price unavailable' : new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(n);
