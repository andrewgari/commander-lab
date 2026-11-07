/** The only review image boundary. Every card image is served by the app's own cache: the web route
 * /api/card-image proxies the API's /api/v2/card-images, which fetches from Scryfall once (rate-limited, with our
 * User-Agent) and serves from disk afterwards. Pure URL construction: no fetch, lookup or Scryfall URL in the browser,
 * so a PDF renders with no internet once the cache is warm.
 */
export function cardImage(name: string, size: 'small' | 'normal' | 'large' = 'normal'): string {
 return `/api/card-image?name=${encodeURIComponent(name)}&size=${size}`;
}
