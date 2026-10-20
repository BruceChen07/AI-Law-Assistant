#!/usr/bin/env node
/**
 * Frontend complexity audit: finds god components and hook-heavy functions.
 *
 *   node scripts/audit_frontend_complexity.mjs
 *
 * For every function/component it reports:
 *   lines      - source span
 *   cx         - cyclomatic complexity (1 + branches)
 *   hooks      - number of React hook calls
 *   jsxDepth   - max JSX nesting depth
 *   useState   - stateful hooks (re-render pressure)
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { createRequire } from 'node:module';
import { join, dirname, relative, extname } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB = join(HERE, '..', 'web');
// @babel/parser is installed under web/node_modules, so resolve from there.
const require = createRequire(join(WEB, 'package.json'));
const { parse } = require('@babel/parser');
const SKIP = new Set(['node_modules', 'storybook-static', '.storybook', 'dist', '.vite']);

const files = [];
(function walk(d) {
  for (const e of readdirSync(d)) {
    if (SKIP.has(e)) continue;
    const p = join(d, e);
    if (statSync(p).isDirectory()) walk(p);
    else if (['.jsx', '.js', '.ts', '.tsx', '.mjs'].includes(extname(p))) files.push(p);
  }
})(WEB);

const HOOKS = new Set(['useState', 'useEffect', 'useMemo', 'useCallback', 'useRef',
  'useReducer', 'useContext', 'useLayoutEffect', 'useTransition', 'useDeferredValue']);

const BRANCH = new Set([
  'IfStatement', 'ConditionalExpression', 'LogicalExpression', 'SwitchCase',
  'ForStatement', 'ForInStatement', 'ForOfStatement', 'WhileStatement',
  'DoWhileStatement', 'CatchClause', 'OptionalMemberExpression',
  'OptionalCallExpression', 'CoalesceExpression',
]);

const FUNC = new Set(['FunctionDeclaration', 'ArrowFunctionExpression',
  'FunctionExpression', 'ObjectMethod', 'ClassMethod']);

function key(k) {
  return !['loc', 'start', 'end', 'range', 'leadingComments', 'trailingComments',
    'innerComments', 'extra', 'comments', 'tokens'].includes(k);
}

function makeCounter() {
  return { cx: 0, hooks: 0, useStates: 0, effects: 0, jsxDepth: 0 };
}

function walk(node, c, depth = 0, cbDepth = 0) {
  if (!node || typeof node.type !== 'string') return;
  if (BRANCH.has(node.type)) c.cx++;
  if (node.type === 'JSXElement' || node.type === 'JSXFragment') {
    c.jsxDepth = Math.max(c.jsxDepth, cbDepth + 1);
  }
  if (node.type === 'CallExpression' && node.callee && node.callee.type === 'Identifier') {
    const n = node.callee.name;
    if (HOOKS.has(n)) {
      c.hooks++;
      if (n === 'useState' || n === 'useReducer') c.useStates++;
      if (n === 'useEffect' || n === 'useLayoutEffect') c.effects++;
    }
  }
  const nextCb = FUNC.has(node.type) ? cbDepth + 1 : cbDepth;
  for (const k of Object.keys(node)) {
    if (!key(k)) continue;
    const v = node[k];
    if (Array.isArray(v)) for (const x of v) walk(x, c, depth + 1, nextCb);
    else if (v && typeof v === 'object') walk(v, c, depth + 1, nextCb);
  }
}

const results = [];
const fileRows = [];

for (const f of files) {
  const src = readFileSync(f, 'utf8');
  let ast;
  try {
    ast = parse(src, {
      sourceType: 'module',
      plugins: ['jsx', 'typescript'],
      errorRecovery: true,
      ranges: false,
    });
  } catch (e) {
    fileRows.push({ file: relative(WEB, f), error: String(e.message || e) });
    continue;
  }
  const rel = relative(WEB, f);
  const lines = src.split('\n').length;

  // top-level functions (incl. const Foo = () => ...)
  function visit(node, nameHint = '') {
    if (!node || typeof node.type !== 'string') return;
    if (FUNC.has(node.type)) {
      const name = node.id?.name || node.key?.name || nameHint || '<anonymous>';
      const c = makeCounter();
      c.cx = 1;
      walk(node.body, c, 0, 0);
      // re-walk counting branch nodes inside body only
      results.push({
        file: rel,
        name,
        line: node.loc?.start.line ?? 0,
        lines: (node.loc?.end.line ?? 0) - (node.loc?.start.line ?? 0) + 1,
        cx: c.cx,
        hooks: c.hooks,
        useState: c.useStates,
        effects: c.effects,
        jsxDepth: c.jsxDepth,
      });
      return; // do not descend: nested callbacks would duplicate
    }
    if (node.type === 'VariableDeclaration') {
      for (const d of node.declarations ?? []) {
        visit(d.init, d.id?.name || '');
      }
      return;
    }
    for (const k of Object.keys(node)) {
      if (!key(k)) continue;
      const v = node[k];
      if (Array.isArray(v)) for (const x of v) visit(x, nameHint);
      else if (v && typeof v === 'object') visit(v, nameHint);
    }
  }
  visit(ast.program);

  fileRows.push({ file: rel, lines });
}

fileRows.sort((a, b) => b.lines - a.lines);
results.sort((a, b) => b.lines - a.lines);

console.log('='.repeat(92));
console.log('FRONTEND COMPLEXITY AUDIT');
console.log('='.repeat(92));
console.log(`files scanned : ${files.length}`);
console.log(`functions     : ${results.length}`);
console.log(`> 200 lines   : ${results.filter((r) => r.lines > 200).length}`);
console.log(`> 100 lines   : ${results.filter((r) => r.lines > 100).length}`);
console.log(`cx > 20       : ${results.filter((r) => r.cx > 20).length}`);
console.log(`useState >= 8 : ${results.filter((r) => r.useState >= 8).length}`);

const pad = (s, n) => String(s).padEnd(n);
const rpad = (s, n) => String(s).padStart(n);

console.log(`\nFILES BY SIZE`);
console.log('-'.repeat(92));
for (const r of fileRows.slice(0, 12)) {
  console.log(`  ${rpad(r.lines, 6)} lines   ${r.file}`);
}

console.log(`\nTOP 20 FUNCTIONS BY LENGTH`);
console.log('-'.repeat(92));
console.log(`  ${pad('lines', 6)}${pad('cx', 5)}${pad('hooks', 7)}${pad('state', 7)}${pad('jsxNest', 8)}name  (file:line)`);
for (const r of results.slice(0, 20)) {
  console.log(`  ${pad(r.lines, 6)}${pad(r.cx, 5)}${pad(r.hooks, 7)}${pad(r.useState, 7)}${pad(r.jsxDepth, 8)}${r.name}  (${r.file}:${r.line})`);
}

console.log(`\nTOP 15 BY CYCLOMATIC COMPLEXITY`);
console.log('-'.repeat(92));
for (const r of [...results].sort((a, b) => b.cx - a.cx).slice(0, 15)) {
  console.log(`  cx=${pad(r.cx, 5)}lines=${pad(r.lines, 6)}${r.name}  (${r.file}:${r.line})`);
}

console.log(`\nTOP 12 BY STATE PRESSURE (useState/useReducer)`);
console.log('-'.repeat(92));
for (const r of [...results].sort((a, b) => b.useState - a.useState).slice(0, 12)) {
  console.log(`  state=${pad(r.useState, 4)}hooks=${pad(r.hooks, 4)}lines=${pad(r.lines, 6)}${r.name}  (${r.file}:${r.line})`);
}

console.log(`\nTOP 12 BY EFFECT COUNT (useEffect/useLayoutEffect)`);
console.log('-'.repeat(92));
for (const r of [...results].sort((a, b) => b.effects - a.effects).slice(0, 12)) {
  console.log(`  effects=${pad(r.effects, 4)}hooks=${pad(r.hooks, 4)}lines=${pad(r.lines, 6)}${r.name}  (${r.file}:${r.line})`);
}
