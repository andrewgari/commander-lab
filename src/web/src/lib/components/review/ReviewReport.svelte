<script lang="ts">
 import './review.css';
 import Verdict from './Verdict.svelte'; import DeckIdentity from './DeckIdentity.svelte'; import Scorecard from './Scorecard.svelte'; import Strengths from './Strengths.svelte'; import Problems from './Problems.svelte'; import TopChanges from './TopChanges.svelte'; import SwapList from './SwapList.svelte'; import CutsToConsider from './CutsToConsider.svelte'; import Bracket from './Bracket.svelte'; import Mana from './Mana.svelte'; import Questions from './Questions.svelte'; import DebugAppendix from './DebugAppendix.svelte'; import Forge from './Forge.svelte'; import Section from './Section.svelte';
 import ManaCurve from './ManaCurve.svelte'; import PipsSources from './PipsSources.svelte'; import RoleBreakdown from './RoleBreakdown.svelte'; import SynergyClusters from './SynergyClusters.svelte';
 import type { ReviewDocument } from './types';
 import type { Snippet } from 'svelte'; import type { Swap } from './types';
 let { document, theme = 'dark', print = false, swapActions }: { document: ReviewDocument; theme?: 'dark' | 'light'; print?: boolean; swapActions?: Snippet<[Swap]> } = $props();
</script>
<article class="review-report" data-theme={theme} class:print-mode={print} aria-label="Deck scouting report">
 <header class="report-header"><div><p class="eyebrow">Commander Lab / Scouting report</p><h1>{document.meta.deck_name}</h1><p class="card-names">{document.meta.commanders.map(c=>c.name).join(' + ')}</p></div><div class="report-stamp"><span class="eyebrow">Reviewed</span><time datetime={document.meta.generated_at}>{document.meta.generated_at.replace('T',' · ').replace('Z',' UTC')}</time><span class="eyebrow">{document.schema_version}</span></div></header>
 <Verdict verdict={document.verdict}/>{#if document.top_changes}<TopChanges changes={document.top_changes} swaps={document.swaps}/>{/if}
 <div class="evidence-intro"><span class="eyebrow">The evidence</span><span class="muted">Assessment against this deck’s own plan</span></div>
 <DeckIdentity identity={document.deck_identity}/><Scorecard scores={document.scorecard}/>
 <div class="findings-grid"><Strengths findings={document.strengths}/><Problems findings={document.problems}/></div>
 <SwapList swaps={document.swaps} topIds={document.top_changes?.swap_ids ?? []} {swapActions}/><CutsToConsider cuts={document.cuts_to_consider}/>
 <div class="assessment-grid"><Bracket bracket={document.bracket}/><Mana mana={document.mana}/></div>
 <Section title="By the numbers" code="09"><div class="chart-grid"><ManaCurve data={document.charts.curve}/><PipsSources data={document.charts.pips_vs_sources}/><RoleBreakdown data={document.charts.roles}/><SynergyClusters data={document.charts.synergy_clusters}/></div></Section>
 <Forge forge={document.forge}/><Questions questions={document.questions_for_owner}/><DebugAppendix {document}/>
 <footer class="report-footer">Commander Lab · Scouting report <span>Card images © Wizards of the Coast · via Scryfall</span></footer>
</article>
