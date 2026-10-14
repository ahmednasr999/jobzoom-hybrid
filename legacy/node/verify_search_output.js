const fs = require('fs');
const path = require('path');

const dir = __dirname;
const inputPath = path.join(dir, 'linkedin_daily_search_plan.csv');
const outputPath = path.join(dir, 'linkedin_job_links.csv');
const logPath = path.join(dir, 'scrape_run.log');

// Parse input (same naive CSV assumption as original script)
const inputLines = fs.readFileSync(inputPath, 'utf8').trim().split(/\r?\n/);
const inputHeader = inputLines[0];
const inputRows = inputLines.slice(1).map((line, idx) => {
  const parts = line.split(',');
  return {
    row: idx + 2,
    country: parts[0].trim(),
    jobTitle: parts.slice(1, -1).join(',').trim(),
    url: parts[parts.length - 1].trim(),
    key: parts[0].trim() + '\u0000' + parts.slice(1, -1).join(',').trim()
  };
});

function parseCsvLine(line) {
  const out = [];
  let cur = '';
  let inQuotes = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
    } else if (ch === ',' && !inQuotes) {
      out.push(cur);
      cur = '';
    } else {
      cur += ch;
    }
  }
  out.push(cur);
  return out;
}

const outputLines = fs.readFileSync(outputPath, 'utf8').trim().split(/\r?\n/);
const outputHeader = outputLines[0];
const outputRows = outputLines.slice(1).map((line, idx) => {
  const fields = parseCsvLine(line);
  return {
    line: idx + 2,
    country: (fields[0] || '').trim(),
    jobTitle: (fields[1] || '').trim(),
    jobUrl: (fields[2] || '').trim(),
    key: (fields[0] || '').trim() + '\u0000' + (fields[1] || '').trim()
  };
});

const urlRegex = /^https:\/\/[a-z0-9-]+\.linkedin\.com\/jobs\/view\/.+-\d+$/;
const malformed = outputRows.filter(r => !r.jobUrl || !urlRegex.test(r.jobUrl) || /\s/.test(r.jobUrl));
const jobIdOf = (url) => {
  const m = url.match(/-(\d+)$/);
  return m ? m[1] : null;
};

const jobIds = outputRows.map(r => jobIdOf(r.jobUrl)).filter(Boolean);
const uniqueJobIds = new Set(jobIds);

// Duplicate composite records
const compositeCounts = new Map();
for (const r of outputRows) {
  const id = jobIdOf(r.jobUrl) || '<no-id>';
  const composite = r.country + '\u0000' + r.jobTitle + '\u0000' + id;
  compositeCounts.set(composite, (compositeCounts.get(composite) || 0) + 1);
}
const duplicateComposites = [...compositeCounts.entries()]
  .filter(([, c]) => c > 1)
  .map(([k, c]) => {
    const [country, jobTitle, id] = k.split('\u0000');
    return { country, jobTitle, jobId: id, count: c };
  });

// Per-search counts, including zero-result searches
const countBySearch = new Map();
for (const r of outputRows) {
  countBySearch.set(r.key, (countBySearch.get(r.key) || 0) + 1);
}
const inputDistinctKeys = new Set(inputRows.map(r => r.key));
const representedKeys = new Set(outputRows.map(r => r.key));
const absentSearches = inputRows.filter(r => !countBySearch.has(r.key));

const summaryLines = ['Country,Job Title,Collected Links,Status'];
for (const r of inputRows) {
  const n = countBySearch.get(r.key) || 0;
  // Escape title for CSV if needed
  const titleCsv = /[",\n]/.test(r.jobTitle) ? '"' + r.jobTitle.replace(/"/g, '""') + '"' : r.jobTitle;
  const countryCsv = /[",\n]/.test(r.country) ? '"' + r.country.replace(/"/g, '""') + '"' : r.country;
  summaryLines.push(`${countryCsv},${titleCsv},${n},${n > 0 ? 'completed_with_links' : 'completed_zero_results'}`);
}
fs.writeFileSync(path.join(dir, 'search_summary.csv'), summaryLines.join('\n') + '\n');

// Parse log for progress evidence
let logText = '';
try { logText = fs.readFileSync(logPath, 'utf8'); } catch (e) { logText = ''; }
const progressRegex = /\[(\d+)\/150\]\s+(.+?)\s+\|\s+(.+?)\s+\|\s+start=(\d+)\s+page=(\d+)\s+new=(\d+)\s+totalLinks=(\d+)/g;
const lastProgressByIndex = new Map();
let m;
while ((m = progressRegex.exec(logText)) !== null) {
  lastProgressByIndex.set(Number(m[1]), {
    country: m[2].trim(),
    title: m[3].trim(),
    start: Number(m[4]),
    page: Number(m[5]),
    newLinks: Number(m[6]),
    totalLinks: Number(m[7])
  });
}
const logHasFetchError = /fetch error/i.test(logText);
const logHasDone = /Done\. Total links:/i.test(logText);
const logDoneMatch = logText.match(/Done\. Total links:\s*(\d+)/);
const logDistinctMarkers = new Set([...logText.matchAll(/\[(\d+)\/150\]/g)].map(x => Number(x[1]))).size;

// Zero-result searches whose final logged attempt was start=0 page=0
const zeroLogConfirmed = absentSearches.filter(r => {
  // find index of any matching input row in log by scanning entries
  const entries = [...lastProgressByIndex.entries()];
  return entries.some(([idx, e]) =>
    e.country === r.country && e.title === r.jobTitle && e.start === 0 && e.page === 0 && e.newLinks === 0 && e.totalLinks === 0
  );
}).length;

const report = {
  inputFile: 'linkedin_daily_search_plan.csv',
  outputFile: 'linkedin_job_links.csv',
  logFile: 'scrape_run.log',
  input: {
    dataRows: inputRows.length,
    distinctCountryTitleSearches: inputDistinctKeys.size,
    uniqueRowsByIdentity: inputRows.length === new Set(inputRows.map(r => r.row)).size
  },
  output: {
    dataRows: outputRows.length,
    header: outputHeader,
    distinctCountryTitleSearchesRepresented: representedKeys.size,
    globallyUniqueLinkedInJobIds: uniqueJobIds.size,
    rowsWithMissingMalformedUrl: malformed.length,
    malformed_examples: malformed.slice(0, 20).map(r => ({ line: r.line, country: r.country, jobTitle: r.jobTitle, jobUrl: r.jobUrl })),
    duplicateCountryTitleJobIdRecords: duplicateComposites.length,
    duplicate_examples: duplicateComposites.slice(0, 20)
  },
  absentFromOutput: {
    count: absentSearches.length,
    searches: absentSearches.map(r => ({ country: r.country, jobTitle: r.jobTitle })),
    confirmedZeroInSavedLog: zeroLogConfirmed,
    status: (absentSearches.length === 0)
      ? 'none_absent'
      : 'see_scrape_run.log; rows shown there as start=0 page=0 new=0 are empty pages from this run'
  },
  logEvidence: {
    distinctProgressMarkers: logDistinctMarkers,
    hasFetchErrorLines: logHasFetchError,
    hasDoneLine: logHasDone,
    loggedTotalLinks: logDoneMatch ? Number(logDoneMatch[1]) : null,
    lastCompletedProgressMarker: Math.max(...lastProgressByIndex.keys()),
    runTimestampsRecorded: false
  }
};

fs.writeFileSync(path.join(dir, 'verification.json'), JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
