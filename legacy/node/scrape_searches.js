const fs = require('fs');
const csvPath = "C:\\Users\\Ahmed.Nasr\\Downloads\\linkedin_daily_search_plan.csv";
const outPath = "C:\\Users\\Ahmed.Nasr\\Downloads\\linkedin_job_links.csv";

const rows = fs.readFileSync(csvPath, 'utf8').trim().split(/\r?\n/).slice(1)
  .map(line => {
    // naive CSV parse: Country,Job Title,URL (no quoted commas expected)
    const parts = line.split(',');
    // URL is last field; job title may contain spaces but no commas; country no commas
    const url = parts[parts.length-1].trim();
    const jobTitle = parts.slice(1, -1).join(',').trim();
    const country = parts[0].trim();
    return { country, jobTitle, url };
  });

(async () => {
  const out = ['Country,Job Title,Job Link'];
  let total = 0;
  for (let i=0; i<rows.length; i++) {
    const row = rows[i];
    const seen = new Set();
    const links = [];
    // transform search URL to API
    const apiUrl = row.url.replace('/jobs/search/?', '/jobs-guest/jobs/api/seeMoreJobPostings/search?');
    let start = 0;
    while (true) {
      const full = apiUrl + (apiUrl.includes('?') ? '&' : '?') + 'start=' + start;
      let text = '';
      try {
        const res = await fetch(full, { headers: { 'User-Agent': 'Mozilla/5.0' } });
        text = await res.text();
      } catch (e) {
        console.error('fetch error', row.country, row.jobTitle, e.message);
        break;
      }
      const matches = text.match(/https:\/\/[^\s"']+\/jobs\/view\/[^\s"']+/g) || [];
      const pageLinks = [...new Set(matches.map(u => u.replace(/[)"'>]+$/,'').split('?')[0]))];
      let newCount = 0;
      for (const l of pageLinks) {
        if (!seen.has(l)) { seen.add(l); links.push(l); newCount++; }
      }
      process.stderr.write(`\r[${i+1}/${rows.length}] ${row.country} | ${row.jobTitle} | start=${start} page=${pageLinks.length} new=${newCount} totalLinks=${links.length}   `);
      if (pageLinks.length === 0 || newCount === 0) break;
      start += 10;
      // small delay to be polite
      await new Promise(r => setTimeout(r, 250));
      if (start > 2000) break;
    }
    for (const l of links) {
      out.push(`${row.country},${JSON.stringify(row.jobTitle)},${l}`);
      total++;
    }
    // flush partial every row
    fs.writeFileSync(outPath, out.join('\n') + '\n');
  }
  console.error('\nDone. Total links:', total);
})();
