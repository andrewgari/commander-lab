<script lang="ts">import { cardImage } from '#lib/cardImage.js'; let { name }: { name: string } = $props(); let failed = $state(false);</script>
<div class="card-image">{#if failed}<span class="image-fallback">Image unavailable</span>{:else}<img src={cardImage(name, 'small')} alt={name} width="146" height="204" onerror={() => failed = true}/>{/if}</div>
