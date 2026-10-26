import base from '../../../../../tests/fixtures/review_document/example.json';
import noCuts from '../../../../../tests/fixtures/review_document/variants/no-cuts.json';
import many from '../../../../../tests/fixtures/review_document/variants/many-swaps.json';
import long from '../../../../../tests/fixtures/review_document/variants/long-names.json';
import unavailable from '../../../../../tests/fixtures/review_document/variants/unavailable-chart.json';
import gradeA from '../../../../../tests/fixtures/review_document/variants/grade-a.json';
import type { ReviewDocument } from '../components/review/types';
export const variants = ['example', 'no-cuts', 'many-swaps', 'long-names', 'unavailable-chart', 'grade-a'] as const;
export function reviewFixture(variant: string): ReviewDocument {
 const doc = structuredClone(base) as ReviewDocument;
 if (variant === 'no-cuts') doc.cuts_to_consider = noCuts.cuts_to_consider;
 if (variant === 'many-swaps') doc.swaps = Array.from({length:many.repeat_swaps}, (_,i) => ({...doc.swaps[i%doc.swaps.length], id:`sw-${i+1}`}));
 if (variant === 'long-names' && long.long_names) {
  // Existing validated card refs, not invented card names.
  const card = doc.cuts_to_consider[0].card;
  doc.swaps[0].cut = card; doc.swaps[0].add = card;
 }
 if (variant === 'unavailable-chart') doc.charts.roles = {status:'unavailable',reason:unavailable.charts.roles.reason};
 if (variant === 'grade-a' && gradeA.grade_a) {
  // Display stress fixture, not MTG advice. Preserve the exactly-three-swaps contract.
  doc.verdict = {grade:'A',confidence:0.94,headline:'A focused deck with a clear plan. Only small refinements remain.',summary:'The core plan is working. Keep its identity intact; these optional changes are refinements, not a rebuild.'};
  doc.problems = [{...doc.problems[0],severity:'low',claim:'A small consistency refinement remains; no urgent rebuild is needed.'}];
  doc.swaps = doc.top_changes.swap_ids.map(id=>doc.swaps.find(s=>s.id===id)!).map(s=>({...s,resolves_finding_id:doc.problems[0].id}));
  doc.top_changes.why_these_first = 'Optional refinements only. The deck does not need a rebuild.';
  const ids = new Set([...doc.strengths,...doc.problems].map(f=>f.id));
  doc.scorecard = doc.scorecard.map(s=>({...s,score:9,one_line:'Strong fit for the deck’s plan.',finding_ids:s.finding_ids.filter(id=>ids.has(id))}));
  doc.cuts_to_consider=[];
 }
 return doc;
}
