const fs = require('fs');
const path = require('path');

const args = process.argv.slice(2);
const getArg = (name, fallback = null) => {
  const hit = args.find(a => a.startsWith(`--${name}=`));
  return hit ? hit.slice(name.length + 3) : fallback;
};
const limit = getArg('limit') ? Number(getArg('limit')) : Infinity;
const delayMs = getArg('delay') ? Number(getArg('delay')) : 750;

const detailsDir = __dirname;
const inputPath = getArg('input') || 'C:\\Users\\Ahmed.Nasr\\Downloads\\linkedin_job_links.csv';
const checkpointPath = path.join(detailsDir, 'job_details_checkpoint.jsonl');
const errorPath = path.join(detailsDir, 'job_details_errors.jsonl');
const outputPath = path.join(detailsDir, 'linkedin_job_details.csv');

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') { cur += '"'; i++; }
      else { inQuotes = !inQuotes; }
    } else if (ch === ',' && !inQuotes) {
      out.push(cur); cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

function csvField(v) {
  const s = String(v ?? '');
  if (/[",\r\n]/.test(s)) return '"' + s.replace(/"/g, '""') + '"';
  return s;
}

function stripTags(s) {
  return s.replace(/<br\s*\/?>/gi, '\n').replace(/<[^>]+>/g, '');
}

const ENTITY_MAP = {
  amp: '&', lt: '<', gt: '>', quot: '"', apos: "'", nbsp: ' ',
  rsquo: '’', lsquo: '‘', rdquo: '”', ldquo: '“', hellip: '…',
  mdash: '—', ndash: '–', copy: '©', reg: '®', trade: '™'
};

function decodeEntities(s) {
  return s.replace(/&(#x?[0-9a-fA-F]+|[a-zA-Z]+);/g, (m, code) => {
    if (code[0] === '#') {
      const n = code[1].toLowerCase() === 'x'
        ? parseInt(code.slice(2), 16)
        : parseInt(code.slice(1), 10);
      if (Number.isFinite(n)) return String.fromCodePoint(n);
      return m;
    }
    return Object.prototype.hasOwnProperty.call(ENTITY_MAP, code) ? ENTITY_MAP[code] : m;
  });
}

function cleanText(s) {
  return decodeEntities(s)
    .replace(/\r/g, '')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function findMarkupDiv(html) {
  const marker = 'show-more-less-html__markup';
  const idx = html.indexOf(marker);
  if (idx < 0) return null;
  const openEnd = html.indexOf('>', idx);
  if (openEnd < 0) return null;
  const start = openEnd + 1;
  let depth = 0;
  let pos = start;
  const lower = html.toLowerCase();
  while (true) {
    const nextOpen = lower.indexOf('<div', pos);
    const nextClose = lower.indexOf('</div>', pos);
    if (nextClose === -1) return html.slice(start);
    if (nextOpen !== -1 && nextOpen < nextClose) {
      depth++;
      pos = nextOpen + 4;
    } else {
      if (depth === 0) return html.slice(start, nextClose);
      depth--;
      pos = nextClose + 6;
    }
  }
}

function extractTitle(html) {
  const m = html.match(/<h2[^>]*top-card-layout__title[^>]*>([\s\S]*?)<\/h2>/i)
          || html.match(/<h2[^>]*class="[^"]*"[^>]*>([\s\S]*?)<\/h2>/i);
  if (!m) return null;
  return cleanText(stripTags(m[1]));
}

function extractCompany(html) {
  const m = html.match(/<a[^>]*topcard__org-name-link[^>]*>([\s\S]*?)<\/a>/i);
  if (!m) {
    const m2 = html.match(/<span[^>]*topcard__org-name[^>]*>([\s\S]*?)<\/span>/i);
    if (!m2) return null;
    return cleanText(stripTags(m2[1]));
  }
  return cleanText(stripTags(m[1]));
}

function extractLocation(html) {
  const m = html.match(/<span[^>]*topcard__flavor--bullet[^>]*>([\s\S]*?)<\/span>/i);
  if (!m) return null;
  return cleanText(stripTags(m[1]));
}

function extractDescription(html) {
  const markup = findMarkupDiv(html);
  if (markup) {
    let text = markup
      .replace(/<br\s*\/?>/gi, '\n')
      .replace(/<\/p>/gi, '\n\n')
      .replace(/<\/li>/gi, '\n')
      .replace(/<li[^>]*>/gi, '- ')
      .replace(/<\/h[1-6]>/gi, '\n')
      .replace(/<[^>]+>/g, '');
    return cleanText(text);
  }
  return null;
}

function jobIdFromUrl(url) {
  const m = String(url).match(/-(\d+)(?:[?#].*)?$/);
  return m ? m[1] : null;
}

async function fetchWithTimeout(url, options, timeoutMs) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timer);
    return res;
  } catch (e) {
    clearTimeout(timer);
    throw e;
  }
}

async function fetchJob(id) {
  const url = `https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/${id}`;
  let lastError = 'unknown error';
  for (let attempt = 1; attempt <= 3; attempt++) {
    try {
      const res = await fetchWithTimeout(url, {
        headers: {
          'User-Agent': 'Mozilla/5.0',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.9'
        }
      }, 30000);
      const html = await res.text();
      if (!res.ok) {
        lastError = `HTTP ${res.status}`;
        if ((res.status === 429 || res.status >= 500) && attempt < 3) {
          await sleep(attempt * 2000 + Math.floor(Math.random() * 500));
          continue;
        }
        return { id, url, ok: false, status: res.status, error: lastError, html: '' };
      }
      const title = extractTitle(html);
      const company = extractCompany(html);
      const location = extractLocation(html);
      const description = extractDescription(html);
      if (!title && !description) {
        return { id, url, ok: false, status: res.status, error: 'parse_failed_no_title_or_description', html: '' };
      }
      return { id, url, ok: true, status: res.status, title, company, location, description };
    } catch (e) {
      lastError = e.name === 'AbortError' ? 'timeout' : (e.message || String(e));
      if (attempt < 3) {
        await sleep(attempt * 2000 + Math.floor(Math.random() * 500));
        continue;
      }
    }
  }
  return { id, url, ok: false, status: 0, error: lastError, html: '' };
}

function loadCheckpoint() {
  const found = new Map();
  if (!fs.existsSync(checkpointPath)) return found;
  const lines = fs.readFileSync(checkpointPath, 'utf8').trim().split(/\r?\n/).filter(Boolean);
  for (const line of lines) {
    try {
      const obj = JSON.parse(line);
      if (obj && obj.id && obj.ok) found.set(obj.id, obj);
    } catch (e) {
      // ignore corrupt line
    }
  }
  return found;
}

function appendCheckpoint(obj) {
  fs.appendFileSync(checkpointPath, JSON.stringify(obj) + '\n');
}

function appendError(obj) {
  fs.appendFileSync(errorPath, JSON.stringify(obj) + '\n');
}

(async () => {
  console.error('Input:', inputPath);
  console.error('Output:', outputPath);
  console.error('Checkpoint:', checkpointPath);
  console.error('Delay ms:', delayMs, 'Limit:', limit === Infinity ? 'none' : limit);

  // Read input rows
  const lines = fs.readFileSync(inputPath, 'utf8').trim().split(/\r?\n/);
  const dataRows = lines.slice(1);
  const unique = new Map(); // jobId -> representative URL
  for (const line of dataRows) {
    const fields = parseCsvLine(line);
    const jobUrl = (fields[2] || '').trim();
    const id = jobIdFromUrl(jobUrl);
    if (id && !unique.has(id)) unique.set(id, jobUrl);
  }
  console.error('Unique job IDs in input:', unique.size);

  const checkpoint = loadCheckpoint();
  console.error('Already checkpointed:', checkpoint.size);

  const toFetch = [...unique.entries()].filter(([id]) => !checkpoint.has(id));
  console.error('To fetch now:', Math.min(toFetch.length, limit));

  let doneSinceStart = 0;
  let processed = 0;
  const start = Date.now();
  for (const [id, url] of toFetch) {
    if (processed >= limit) break;
    processed++;
    const result = await fetchJob(id);

    if (result.ok) {
      checkpoint.set(id, {
        id,
        url,
        fetchedUrl: result.url,
        title: result.title || '',
        company: result.company || '',
        location: result.location || '',
        description: result.description || '',
        fetchedAt: new Date().toISOString(),
        ok: true
      });
      appendCheckpoint(checkpoint.get(id));
    } else {
      appendError({
        id,
        url,
        status: result.status,
        error: result.error,
        triedAt: new Date().toISOString()
      });
    }

    doneSinceStart++;
    const elapsed = ((Date.now() - start) / 1000).toFixed(1);
    process.stderr.write(`\r[${processed}/${Math.min(toFetch.length, limit)}] ID ${id} ${result.ok ? 'OK' : 'FAIL'} ${result.ok ? (result.title || '').slice(0, 60) : result.error}   elapsed=${elapsed}s   `);

    if (processed < Math.min(toFetch.length, limit)) await sleep(delayMs);
  }
  console.error('\nFetch pass complete. Checkpointed total:', checkpoint.size);

  // Build final merged CSV from checkpoint
  const detailsById = new Map(checkpoint);
  // also load any checkpoint lines not in memory? Already loaded all.
  const outHeader = ['Country', 'Job Title', 'Job Link', 'Job ID', 'LinkedIn Job Title', 'Company', 'Location', 'Job Description'];
  const outLines = [outHeader.join(',')];

  let missing = 0;
  for (const line of dataRows) {
    const fields = parseCsvLine(line);
    const country = (fields[0] || '').trim();
    const searchTitle = (fields[1] || '').trim();
    const jobUrl = (fields[2] || '').trim();
    const id = jobIdFromUrl(jobUrl);
    const d = id ? detailsById.get(id) : null;
    if (!d) missing++;
    outLines.push([
      csvField(country),
      csvField(searchTitle),
      csvField(jobUrl),
      csvField(id || ''),
      csvField(d ? d.title : ''),
      csvField(d ? d.company : ''),
      csvField(d ? d.location : ''),
      csvField(d ? d.description : '')
    ].join(','));
  }

  fs.writeFileSync(outputPath, outLines.join('\n') + '\n');
  console.error('Wrote:', outputPath);
  console.error('Rows:', outLines.length - 1, 'Missing details:', missing);
})();
