<script lang="ts">
 import type { ReviewDocument } from './types'; import { percent } from './types';
 let { verdict }: { verdict: ReviewDocument['verdict'] } = $props();
</script>
<section class="verdict" aria-label="Verdict"><div class="grade"><span class="eyebrow">Grade</span><strong>{verdict.grade}</strong><span class="eyebrow">{percent(verdict.confidence)} confidence</span></div><div><p class="eyebrow">The verdict</p><h2>{verdict.headline}</h2><p>{verdict.summary}</p></div></section>
