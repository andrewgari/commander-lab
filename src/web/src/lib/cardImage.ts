/** Only review image boundary. Card 11 replaces this URL with its local cache.
 * Pure URL construction: no fetch, lookup, or server network during rendering.
 */
export function cardImage(name: string, size: 'small' | 'normal' | 'large' = 'normal'): string {
 return `https://api.scryfall.com/cards/named?exact=${encodeURIComponent(name)}&format=image&version=${size}`;
}
