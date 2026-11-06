import { reviewFixture, variants } from '#lib/server/reviewFixtures.js';
import type { PageServerLoad } from './$types';
export const load: PageServerLoad = ({ url }) => {
 const requested = url.searchParams.get('variant') ?? 'example';
 const variant = variants.find(v=>v===requested) ?? 'example';
 return { document: reviewFixture(variant), variant, variants, print: url.searchParams.get('print') === '1', theme: url.searchParams.get('theme') === 'light' ? 'light' as const : 'dark' as const };
};
