const UPSTREAM = 'https://raw.githubusercontent.com/tapferkeittenebris/wyoming-election-results-2026/main/results.json';

export async function GET() {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 7000);
  const cacheBucket = Math.floor(Date.now() / 30000);

  try {
    const upstream = await fetch(`${UPSTREAM}?v=${cacheBucket}`, {
      signal: controller.signal,
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

    return new Response(body, {
      status: 200,
      headers: {
        'Content-Type': 'application/json; charset=utf-8',
        'Cache-Control': 'public, max-age=15',
        'Vercel-CDN-Cache-Control': 'public, max-age=45, stale-while-revalidate=300',
        'X-Results-Proxy': 'vercel-edge-cache'
      }
    });
  } catch (error) {
    return Response.json(
      { error: 'results_feed_temporarily_unavailable' },
      {
        status: 502,
        headers: {
          'Cache-Control': 'no-store'
        }
      }
    );
  } finally {
    clearTimeout(timeout);
  }
}
