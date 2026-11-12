// Exercise the dependency overrides using owned fixtures, without running targets.
import { createRequire } from 'node:module';
import { mkdir, writeFile } from 'node:fs/promises';
import { resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import assert from 'node:assert/strict';

const require = createRequire(new URL('../runtime/rea/package.json', import.meta.url));
const { z } = require('zod');
const { minimatch } = require('minimatch');
const { globSync } = require('glob');
const asar = require('@electron/asar');
const root = fileURLToPath(new URL('../.test-runs/rea-dependency-smoke/', import.meta.url));
await mkdir(resolve(root, 'source/nested'), { recursive: true });
await writeFile(resolve(root, 'source/nested/fixture.js'), 'export const answer = 42;\n');
await writeFile(resolve(root, 'source/readme.txt'), 'HAYAT owned fixture\n');
assert(z.array(z.number()).max(2).safeParse([1, 2]).success);
assert(!z.array(z.number()).max(2).safeParse([1, 2, 3]).success);
assert(minimatch('nested/fixture.js', '**/*.js'));
assert(!minimatch('nested/fixture.txt', '**/*.js'));
assert.deepEqual(globSync('**/*.js', { cwd: resolve(root, 'source') }).map(name => name.replaceAll('\\', '/')),
                 ['nested/fixture.js']);
await asar.createPackage(resolve(root, 'source'), resolve(root, 'fixture.asar'));
assert(asar.listPackage(resolve(root, 'fixture.asar')).some(name => name.endsWith('fixture.js')));
assert.equal(asar.extractFile(resolve(root, 'fixture.asar'), 'nested/fixture.js').toString(),
             'export const answer = 42;\n');
const report = { passed: true, checked_at: new Date().toISOString(), node: process.version,
                 checks: ['schema validation', 'filename matcher', 'glob traversal', 'asar create/list/read'] };
await writeFile(resolve(root, 'verification.json'), JSON.stringify(report, null, 2));
console.log(JSON.stringify(report));
