// Weekly sprint digest for a GitHub Projects (v2) board -> Telegram.
//
// Reads the current iteration from the project and reports Done/total progress,
// then lists not-done work: Assigned (grouped by user, then epic) and
// Unassigned (grouped by epic).
//
// Self-contained: no npm deps, uses Node 20+ global fetch.
//
// GitHub login -> Telegram handle mapping lives in ./telegram-users.json
// (mapped users are shown as @handle and get pinged; unmapped fall back to
// their GitHub login).
//
// Required env:
//   GH_TOKEN         PAT with read:project (+ repo for private repo issue data)
//   PROJECT_OWNER    e.g. "decker757"
//   PROJECT_NUMBER   the project number from its URL (.../projects/<N>)
// Optional env (if set, the digest is also sent to Telegram; otherwise it just prints):
//   TELEGRAM_BOT_TOKEN
//   TELEGRAM_CHAT_ID
//   STATUS_DONE      comma-separated status names counted as "done" (default "Done")

import { readFileSync } from 'node:fs';

const {
  GH_TOKEN,
  PROJECT_OWNER,
  PROJECT_NUMBER,
  TELEGRAM_BOT_TOKEN,
  TELEGRAM_CHAT_ID,
  STATUS_DONE = 'Done',
} = process.env;

if (!GH_TOKEN || !PROJECT_OWNER || !PROJECT_NUMBER) {
  console.error('Missing required env: GH_TOKEN, PROJECT_OWNER, PROJECT_NUMBER');
  process.exit(1);
}

const DONE_NAMES = new Set(STATUS_DONE.split(',').map((s) => s.trim().toLowerCase()));

// Query both user- and org-owned projects; whichever exists is non-null.
const QUERY = `
query($owner:String!, $number:Int!, $cursor:String) {
  user(login:$owner)         { ...proj }
  organization(login:$owner) { ...proj }
}
fragment proj on ProjectV2Owner {
  projectV2(number:$number) {
    title
    fields(first:50) {
      nodes {
        __typename
        ... on ProjectV2IterationField {
          name
          configuration {
            iterations           { id title startDate duration }
            completedIterations  { id title startDate duration }
          }
        }
      }
    }
    items(first:100, after:$cursor) {
      pageInfo { hasNextPage endCursor }
      nodes {
        content {
          __typename
          ... on Issue        { number title url state assignees(first:10){ nodes { login } } labels(first:20){ nodes { name } } }
          ... on PullRequest  { number title url state assignees(first:10){ nodes { login } } labels(first:20){ nodes { name } } }
        }
        fieldValues(first:20) {
          nodes {
            __typename
            ... on ProjectV2ItemFieldSingleSelectValue { name field { ... on ProjectV2SingleSelectField { name } } }
            ... on ProjectV2ItemFieldIterationValue   { iterationId }
          }
        }
      }
    }
  }
}`;

async function gql(cursor) {
  const res = await fetch('https://api.github.com/graphql', {
    method: 'POST',
    headers: {
      Authorization: `bearer ${GH_TOKEN}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      query: QUERY,
      variables: { owner: PROJECT_OWNER, number: Number(PROJECT_NUMBER), cursor },
    }),
  });
  if (!res.ok) throw new Error(`GitHub API ${res.status}: ${await res.text()}`);
  const json = await res.json();
  // We query both user() and organization() for the same login; the one that
  // doesn't match returns a harmless NOT_FOUND. Only surface other errors.
  const realErrors = (json.errors ?? []).filter((e) => e.type !== 'NOT_FOUND');
  if (realErrors.length) throw new Error(`GraphQL: ${JSON.stringify(realErrors)}`);
  return json.data;
}

// Which iteration is "now"? Prefer the one whose date range contains today;
// otherwise fall back to the nearest upcoming iteration.
function pickCurrentIteration(iterationField) {
  const iters = iterationField?.configuration?.iterations ?? [];
  const today = new Date();
  today.setUTCHours(0, 0, 0, 0);
  let nearest = null;
  let nearestDelta = Infinity;
  for (const it of iters) {
    const start = new Date(`${it.startDate}T00:00:00Z`);
    const end = new Date(start);
    end.setUTCDate(end.getUTCDate() + it.duration);
    if (today >= start && today < end) return it;
    const delta = Math.abs(start - today);
    if (delta < nearestDelta) {
      nearestDelta = delta;
      nearest = it;
    }
  }
  return nearest;
}

const esc = (s) =>
  String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

// GitHub login -> Telegram handle. Missing/empty entries fall back to the login.
let USER_MAP = {};
try {
  USER_MAP = JSON.parse(readFileSync(new URL('./telegram-users.json', import.meta.url), 'utf8'));
} catch {
  // No mapping file (or unreadable) — everyone shows as their GitHub login.
}
const mention = (login) => {
  const handle = USER_MAP[login];
  return handle ? `@${esc(handle)}` : esc(login);
};

async function main() {
  // Fetch first project owner that is non-null, paging through items.
  let owner = null;
  let cursor = null;
  const items = [];
  let iterationField = null;
  let projectTitle = 'Project';

  do {
    const data = await gql(cursor);
    const proj = data.user?.projectV2 ?? data.organization?.projectV2;
    if (!proj) {
      console.error(`No project #${PROJECT_NUMBER} found for owner "${PROJECT_OWNER}".`);
      process.exit(1);
    }
    projectTitle = proj.title;
    if (!iterationField) {
      iterationField = proj.fields.nodes.find((f) => f.__typename === 'ProjectV2IterationField');
    }
    items.push(...proj.items.nodes);
    cursor = proj.items.pageInfo.hasNextPage ? proj.items.pageInfo.endCursor : null;
    owner = proj;
  } while (cursor);

  if (!iterationField) {
    console.error('No iteration field found on this project. Is an Iteration field configured?');
    process.exit(1);
  }

  const current = pickCurrentIteration(iterationField);
  if (!current) {
    console.error('No iterations configured on the iteration field.');
    process.exit(1);
  }

  // Keep only items in the current iteration that have an issue/PR behind them.
  const inSprint = items.filter((it) => {
    const c = it.content;
    if (!c || (c.__typename !== 'Issue' && c.__typename !== 'PullRequest')) return false;
    return it.fieldValues.nodes.some(
      (v) => v.__typename === 'ProjectV2ItemFieldIterationValue' && v.iterationId === current.id,
    );
  });

  const statusOf = (it) => {
    const v = it.fieldValues.nodes.find(
      (n) => n.__typename === 'ProjectV2ItemFieldSingleSelectValue' && n.field?.name === 'Status',
    );
    return v?.name ?? 'No status';
  };
  const isDone = (it) =>
    it.content.state === 'CLOSED' || DONE_NAMES.has(statusOf(it).toLowerCase());

  const total = inSprint.length;
  const done = inSprint.filter(isDone).length;
  const pct = total ? Math.round((done / total) * 100) : 0;

  // Split remaining (not-done) work into assigned vs unassigned,
  // each broken down by epic (from the "epic:" label).
  const remaining = inSprint.filter((it) => !isDone(it));

  const assigneesOf = (it) => it.content.assignees?.nodes.map((a) => a.login) ?? [];
  const epicOf = (it) => {
    const label = (it.content.labels?.nodes ?? []).find((l) => /^epic:/i.test(l.name));
    return label ? label.name.replace(/^epic:\s*/i, '') : 'No epic';
  };
  const groupByEpic = (items) => {
    const m = new Map();
    for (const it of items) {
      const e = epicOf(it);
      if (!m.has(e)) m.set(e, []);
      m.get(e).push(it);
    }
    return m;
  };
  // Epics alphabetical, "No epic" last.
  const sortedEpics = (m) =>
    [...m.keys()].sort((a, b) => {
      if (a === 'No epic') return 1;
      if (b === 'No epic') return -1;
      return a.localeCompare(b);
    });

  // Render items grouped by epic, at a given indent.
  const renderEpics = (items, indent) => {
    const out = [];
    const byEpic = groupByEpic(items);
    for (const epic of sortedEpics(byEpic)) {
      out.push(`${indent}🏷 <b>${esc(epic)}</b>`);
      for (const it of byEpic.get(epic)) {
        const c = it.content;
        out.push(
          `${indent}  • [${esc(statusOf(it))}] <a href="${esc(c.url)}">#${c.number}</a> ${esc(c.title)}`,
        );
      }
    }
    return out;
  };

  // Build the Telegram message (HTML parse_mode).
  const lines = [];
  lines.push(`📊 <b>${esc(projectTitle)} — Sprint Digest</b>`);
  lines.push(`🗓 Iteration: <b>${esc(current.title)}</b>`);
  lines.push(`📈 Progress: <b>${done}/${total}</b> done (${pct}%)`);
  lines.push('');

  if (!remaining.length) {
    lines.push('🎉 Nothing left — all sprint items are done!');
  } else {
    const assigned = remaining.filter((it) => assigneesOf(it).length > 0);
    const unassigned = remaining.filter((it) => assigneesOf(it).length === 0);

    if (assigned.length) {
      lines.push('✅ <b>Assigned</b>');
      // Group by user (an item with multiple assignees appears under each).
      const byUser = new Map();
      for (const it of assigned) {
        for (const login of assigneesOf(it)) {
          if (!byUser.has(login)) byUser.set(login, []);
          byUser.get(login).push(it);
        }
      }
      for (const login of [...byUser.keys()].sort((a, b) => a.localeCompare(b))) {
        lines.push(`  👤 <b>${mention(login)}</b>`);
        lines.push(...renderEpics(byUser.get(login), '    '));
      }
      lines.push('');
    }

    if (unassigned.length) {
      lines.push('🧟 <b>Unassigned</b>');
      lines.push(...renderEpics(unassigned, '  '));
      lines.push('');
    }
  }

  const message = lines.join('\n').trim();
  console.log(message);

  if (TELEGRAM_BOT_TOKEN && TELEGRAM_CHAT_ID) {
    const res = await fetch(
      `https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          chat_id: TELEGRAM_CHAT_ID,
          text: message,
          parse_mode: 'HTML',
          disable_web_page_preview: true,
        }),
      },
    );
    if (!res.ok) throw new Error(`Telegram ${res.status}: ${await res.text()}`);
    console.error('Sent to Telegram.');
  } else {
    console.error('TELEGRAM_* not set — printed digest only, did not send.');
  }
}

main().catch((err) => {
  console.error(err.message ?? err);
  process.exit(1);
});
