// GET /api/stats — aggregate usage numbers for the wiki footer.
//
// Combines three trust-safe sources, all anonymous / public:
//   web    : agent-vs-human page views to vibetrading.wiki (last 30 days),
//            counted server-side by _middleware.js into D1.
//   pypi   : install counts for `vibe-trading-ai` (pypistats public API).
//   github : repository stars (GitHub public API).
//
// Never throws to the client: on any failure it returns zeros so the footer
// degrades gracefully.

const WINDOW_DAYS = 30;

async function fetchPypi() {
  try {
    const resp = await fetch(
      "https://pypistats.org/api/packages/vibe-trading-ai/recent",
      { headers: { "User-Agent": "vibetrading-wiki" }, cf: { cacheTtl: 1800 } },
    );
    if (!resp.ok) return null;
    const data = await resp.json();
    return {
      last_day: data?.data?.last_day ?? 0,
      last_week: data?.data?.last_week ?? 0,
      last_month: data?.data?.last_month ?? 0,
    };
  } catch {
    return null;
  }
}

async function fetchGithub() {
  try {
    const resp = await fetch("https://api.github.com/repos/HKUDS/Vibe-Trading", {
      headers: { "User-Agent": "vibetrading-wiki", Accept: "application/vnd.github+json" },
      cf: { cacheTtl: 1800 },
    });
    if (!resp.ok) return null;
    const repo = await resp.json();
    return { stars: repo?.stargazers_count ?? 0 };
  } catch {
    return null;
  }
}

export async function onRequest(context) {
  const { env } = context;
  const headers = {
    "Access-Control-Allow-Origin": "*",
    "Cache-Control": "public, max-age=60",
  };

  const web = { human: 0, agent: 0, bot: 0, window_days: WINDOW_DAYS };
  try {
    if (env.DB) {
      const since = new Date(Date.now() - WINDOW_DAYS * 86400000)
        .toISOString()
        .slice(0, 10);
      const { results } = await env.DB.prepare(
        "SELECT klass, SUM(n) AS total FROM visits WHERE day >= ?1 GROUP BY klass",
      )
        .bind(since)
        .all();
      for (const row of results || []) {
        if (row.klass in web) web[row.klass] = Number(row.total) || 0;
      }
    }
  } catch {
    // leave zeros
  }

  const [pypi, github] = await Promise.all([fetchPypi(), fetchGithub()]);

  return Response.json(
    {
      web,
      pypi,
      github,
      note: "anonymous · first-party · aggregate sample",
      generated_at: new Date().toISOString(),
    },
    { headers },
  );
}
