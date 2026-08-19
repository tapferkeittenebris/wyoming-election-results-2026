const UPSTREAM = 'https://raw.githubusercontent.com/tapferkeittenebris/wyoming-election-results-2026/main/results.json';

export default async function handler(request, response) {
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.setHeader('Allow', 'GET, HEAD');
    return response.status(405).json({ error: 'method_not_allowed' });
  }

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 7000);
  const cacheBuster = Date.now();

  try {
    const upstream = await fetch(`${UPSTREAM}?v=${cacheBuster}`, {
      signal: controller.signal,
      cache: 'no-store',
      headers: {
        'Accept': 'application/json',
        'User-Agent': 'Buck-the-Freedom-Caucus-results-proxy'
      }
    });

    if (!upstream.ok) {
      throw new Error(`GitHub results feed returned ${upstream.status}`);
    }

    const body = await upstream.text();
    JSON.parse(body);

    response.setHeader('Content-Type', 'application/json; charset=utf-8');
    response.setHeader('Cache-Control', 'no-store, max-age=0, must-revalidate');
    response.setHeader('Vercel-CDN-Cache-Control', 'no-store');
    response.setHeader('X-Results-Proxy', 'live-upstream');
    return response.status(200).send(request.method === 'HEAD' ? '' : body);
  } catch (error) {
    response.setHeader('Cache-Control', 'no-store');
    return response.status(502).json({ error: 'results_feed_temporarily_unavailable' });
  } finally {
    clearTimeout(timeout);
  }
}
