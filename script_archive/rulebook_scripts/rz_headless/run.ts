// Headless Rosterizer pipeline runner: node out.mjs <hostBook> <rosterJsonPath> [battleSize]
import { runPipeline } from '@app/helpers/smoke-pipeline';
import * as fs from 'fs';

const DIR = '/Users/kduda/websites/RosterizerTestData/Warhammer40k11e/';
const books: Record<string, any> = {};
for (const f of fs.readdirSync(DIR)) {
  if (f.endsWith('.rulebook')) {
    const d = JSON.parse(fs.readFileSync(DIR + f, 'utf8'));
    books[d.name.trim()] = d;
    books[f.replace('.rulebook', '')] = d;
  }
}
function chain(b: any): any[] {
  const out: any[] = [];
  for (const dep of b.dependencies ?? []) {
    const a = books[dep.name.trim()];
    for (const x of [...chain(a), a]) if (!out.includes(x)) out.push(x);
  }
  return out;
}

const [host, rosterPath] = process.argv.slice(2);
const fixture = books[host];
const imports = chain(fixture).map((b) => b.rulebook);
const roster = JSON.parse(fs.readFileSync(rosterPath, 'utf8'));

const { root } = runPipeline(fixture, imports, { roster });
const r: any = root;
const stat = (a: any, n: string) => {
  const s = a.stats?.[n];
  if (!s) return undefined;
  return { value: s.processed?.numeric?.current ?? s.processed?.rank?.current ?? s.processed?.term?.current ?? s.value, max: s.max };
};
console.log('ROSTER', JSON.stringify({
  'Battle Size': stat(r, 'Battle Size'), Points: stat(r, 'Points'), DPs: stat(r, 'Detachment Points'),
  DetachmentRanks: Object.keys(r.stats?.Detachment?.ranks ?? {}), Enhancements: stat(r, 'Enhancements'), 'Enhancement Count': stat(r, 'Enhancement Count'), tracked: r.tracked,
}));
const walk = (a: any, depth: number) => {
  const errs = (a.errors ?? []).map((e: any) => `${e.type}: ${e.message}`);
  const extra: any = {};
  for (const n of ['maxQty', 'Points', 'Enhancements']) {
    const s = stat(a, n);
    if (s !== undefined) extra[n] = s.value;
  }
  console.log(`${'  '.repeat(depth)}${a.item}${a.quantity && a.quantity > 1 ? ' x' + a.quantity : ''} ${JSON.stringify(extra)}${errs.length ? '  ERRORS: ' + errs.join(' | ') : ''}`);
  for (const c of a.assets?.included ?? []) walk(c, depth + 1);
};
walk(r, 0);
