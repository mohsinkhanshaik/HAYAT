"""Parse public JavaScript dependencies with REA's installed Babel parser.

The source is stdin data for an installed parser, never imported or executed.
No npm install, project configuration, source map evaluation or target scripts run.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess


_PARSER = r"""
import { createRequire } from 'node:module';
import { readFileSync } from 'node:fs';
const require = createRequire(new URL('./package.json', `file:///${process.cwd().replaceAll('\\', '/')}/`));
const { parse } = require('@babel/parser');
const source = readFileSync(0, 'utf8');
const result = {dependencies: [], source_map: '', errors: [], parser: '@babel/parser'};
try {
  const ast = parse(source, {sourceType: 'unambiguous', errorRecovery: true,
                           plugins: ['jsx'], createImportExpressions: true});
  const seen = new Set();
  function add(node, kind) {
    const value = node?.type === 'StringLiteral' ? node.value : null;
    if (typeof value !== 'string' || value.length > 2048 || result.dependencies.length >= 64) return;
    const key = kind + '\0' + value;
    if (!seen.has(key)) {seen.add(key); result.dependencies.push({specifier: value, kind});}
  }
  const stack = [ast.program];
  let count = 0;
  while (stack.length && count++ < 100000) {
    const node = stack.pop();
    if (!node || typeof node !== 'object') continue;
    if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(node.type)) add(node.source, 'module');
    if (node.type === 'ImportExpression') add(node.source, 'dynamic_import_literal');
    if (node.type === 'CallExpression' && node.callee?.type === 'Identifier' && node.callee.name === 'require') add(node.arguments?.[0], 'require_literal');
    for (const [key, value] of Object.entries(node)) {
      if (['loc', 'start', 'end', 'extra', 'leadingComments', 'trailingComments', 'innerComments'].includes(key)) continue;
      if (Array.isArray(value)) stack.push(...value.slice().reverse());
      else if (value && typeof value === 'object') stack.push(value);
    }
  }
  if (stack.length) result.errors.push('AST traversal limit; dependency discovery is partial.');
  for (const comment of ast.comments || []) {
    const match = /^[#@]\s*sourceMappingURL\s*=\s*(\S+)\s*$/m.exec(comment.value.trim());
    if (match && match[1].length <= 4096) result.source_map = match[1];
  }
  result.errors.push(...(ast.errors || []).slice(0, 3).map(error => 'Parser: ' + error.reasonCode));
} catch (error) { result.errors.push('Parser: ' + (error.reasonCode || error.name)); }
process.stdout.write(JSON.stringify(result));
"""


def discover_javascript(source: bytes, *, root: Path | None = None, timeout: float = 8) -> dict:
    """Return literal dependency/source-map leads, or explicit parser limitations."""
    from app.config import PROJECT_ROOT
    from app.safety.sandbox import ProcessSandbox

    root = Path(root or PROJECT_ROOT)
    node, package = root / 'runtime/node/node.exe', root / 'runtime/rea'
    missing = {'dependencies': [], 'source_map': '', 'parser': 'unavailable',
               'errors': ['Installed REA Babel parser or bundled Node is unavailable; no import discovery was performed.']}
    if not node.is_file() or not (package / 'node_modules/@babel/parser/package.json').is_file():
        return missing
    if len(source) > 1_000_000:
        return {**missing, 'errors': ['Source exceeds the static dependency parser byte limit.']}
    try:
        environment = ProcessSandbox().environment()
        # A user's Node hooks must not turn parser startup into target execution.
        for key in list(environment):
            if key.upper() in {'NODE_OPTIONS', 'NODE_PATH'}:
                environment.pop(key)
        result = subprocess.run([str(node), '--max-old-space-size=128', '--input-type=module', '-e', _PARSER],
                                input=source.decode('utf-8-sig'), cwd=package, capture_output=True,
                                encoding='utf-8', timeout=max(.1, min(8, timeout)), check=False,
                                env=environment,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        if result.returncode != 0 or len(result.stdout) > 256_000:
            raise ValueError('Installed dependency parser did not produce a bounded result.')
        value = json.loads(result.stdout)
        if not isinstance(value, dict) or not isinstance(value.get('dependencies'), list):
            raise ValueError('Invalid parser response.')
        return value
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError) as error:
        return {**missing, 'errors': ['Static dependency discovery unavailable: ' + type(error).__name__]}


def summarize_source_map(raw: bytes) -> dict:
    """Inspect a published map as evidence; never write/fetch its source paths."""
    import hashlib

    value = json.loads(raw.decode('utf-8-sig'))
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 3:
        raise ValueError('Expected a version 3 source-map JSON object.')
    if 'sections' in value:
        if not isinstance(value['sections'], list):
            raise ValueError('Indexed source-map sections must be an array.')
        return {'version': 3, 'indexed_sections': len(value['sections']), 'embedded_sources': [],
                'source_count': 0, 'scope': 'Indexed map retained as evidence; sections were not expanded or fetched.'}
    sources, content = value.get('sources'), value.get('sourcesContent')
    if not isinstance(sources, list) or any(not isinstance(item, str) for item in sources) or not isinstance(value.get('mappings'), str):
        raise ValueError('Source map requires a source-name array and mappings string.')
    if content is not None and (not isinstance(content, list) or len(content) != len(sources)
                                or any(item is not None and not isinstance(item, str) for item in content)):
        raise ValueError('Source-map embedded content does not match the source-name array.')
    embedded = []
    for name, body in zip(sources[:64], (content or [None] * len(sources))[:64], strict=True):
        if body is not None:
            encoded = body.encode('utf-8')
            embedded.append({'name': name[:1000], 'bytes': len(encoded), 'sha256': hashlib.sha256(encoded).hexdigest()})
    return {'version': 3, 'source_count': len(sources), 'embedded_sources': embedded,
            'source_root': str(value.get('sourceRoot') or '')[:1000],
            'scope': 'Publisher-provided source names/content hashes only; mappings and authorship are not independently verified. '
                     'Original source names are not fetched, executed or used as filesystem paths.'}
