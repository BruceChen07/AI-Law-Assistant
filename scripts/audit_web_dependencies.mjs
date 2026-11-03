#!/usr/bin/env node
/**
 * Audit the frontend dependency declarations.
 *
 *   node scripts/audit_web_dependencies.mjs
 *
 * Checks two things:
 *   1. web/package.json and web/package-lock.json agree (so `npm ci` on a fresh
 *      server installs the same tree that was tested here).
 *   2. every bare import in web/ sources is declared in package.json.
 */
import { readFileSync, readdirSync, statSync, existsSync } from 'node:fs';
import { join, dirname, relative, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, '..', 'web');

const pkg = JSON.parse(readFileSync(join(WEB, 'package.json'), 'utf8'));
const lockPath = join(WEB, 'package-lock.json');
const lock = existsSync(lockPath) ? JSON.parse(readFileSync(lockPath, 'utf8')) : null;

const decl = { ...(pkg.dependencies ?? {}) };
const devDecl = { ...(pkg.devDependencies ?? {}) };
const root = lock?.packages?.[''] ?? {};
const lockDep = root.dependencies ?? {};
const lockDev = root.devDependencies ?? {};

console.log('='.repeat(78));
console.log('FRONTEND DEPENDENCY AUDIT');
console.log('='.repeat(78));
console.log('package.json :', pkg.name, pkg.version);
console.log('lockfile     :', lock ? `v${lock.lockfileVersion} (${lock.name} ${lock.version})` : 'MISSING');

let problems = 0;
const check = (label, list) => {
  console.log(`${label}: ${list.length ? list.join(', ') : 'none'}`);
  problems += list.length;
};

console.log('\n--- manifest vs lockfile ---');
check('missing from lock (deps)', Object.keys(decl).filter((k) => !(k in lockDep)));
check('missing from lock (devDeps)', Object.keys(devDecl).filter((k) => !(k in lockDev)));
check('extra in lock (deps)', Object.keys(lockDep).filter((k) => !(k in decl)));
check('extra in lock (devDeps)', Object.keys(lockDev).filter((k) => !(k in devDecl)));
for (const [k, v] of Object.entries(decl)) {
  if (lockDep[k] && lockDep[k] !== v) {
    console.log(`  SPEC DRIFT ${k}: package.json="${v}" lock="${lockDep[k]}"`);
    problems++;
  }
}
for (const [k, v] of Object.entries(devDecl)) {
  if (lockDev[k] && lockDev[k] !== v) {
    console.log(`  SPEC DRIFT ${k}: package.json="${v}" lock="${lockDev[k]}"`);
    problems++;
  }
}

// ---- bare import specifiers in sources ----
const SKIP = new Set(['node_modules', '.storybook', 'storybook-static', 'dist', '.vite']);
const files = [];
(function walk(d) {
  for (const e of readdirSync(d)) {
    if (SKIP.has(e)) continue;
    const p = join(d, e);
    if (statSync(p).isDirectory()) walk(p);
    else if (['.js', '.jsx', '.ts', '.tsx', '.mjs'].includes(extname(p))) files.push(p);
  }
})(WEB);

const specs = new Map();
const patterns = [
  /(?:^|\s)(?:import|export)[\s\S]*?from\s*['"]([^'"]+)['"]/g,
  /(?:^|\s)import\s*['"]([^'"]+)['"]/g,
  /require\(\s*['"]([^'"]+)['"]\s*\)/g,
];
for (const f of files) {
  const src = readFileSync(f, 'utf8');
  for (const re of patterns) {
    re.lastIndex = 0;
    let m;
    while ((m = re.exec(src))) {
      const raw = m[1];
      if (raw.startsWith('.') || raw.startsWith('/') || raw.startsWith('node:')) continue;
      const clean = raw.replace(/[?#].*$/, '');
      const parts = clean.split('/');
      const name = clean.startsWith('@') ? parts.slice(0, 2).join('/') : parts[0];
      if (!specs.has(name)) specs.set(name, new Set());
      specs.get(name).add(relative(WEB, f));
    }
  }
}

console.log(`\n--- bare specifiers in ${files.length} source files ---`);
const undeclared = [];
for (const [name, fs] of [...specs].sort()) {
  const ok = name in decl || name in devDecl;
  if (!ok) undeclared.push(name);
  console.log(`  ${ok ? 'OK        ' : 'UNDECLARED'} ${name.padEnd(30)} ${fs.size} file(s)`);
}
console.log(`\nundeclared: ${undeclared.length ? undeclared.join(', ') : 'none'}`);
problems += undeclared.length;

console.log(`\nRESULT: ${problems === 0 ? 'OK - declarations match sources and lockfile' : `${problems} problem(s) found`}`);
process.exit(problems === 0 ? 0 : 1);
