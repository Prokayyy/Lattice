"""Download the live Flash OpenAPI spec and diff it against our client usage."""
import json
import re
import urllib.request
from pathlib import Path

import sys

sys.path.insert(0, '.')
import config

SPEC_URL = 'https://flash.definitive.fi/openapi.json'
OUT = Path('analysis/_flash_openapi.json')

req = urllib.request.Request(SPEC_URL, headers={
    'x-definitive-api-key': config.DEFINITIVE_FLASH_API_KEY,
    'Accept': 'application/json',
    'User-Agent': 'lattice-scanner/1.0',
})
with urllib.request.urlopen(req, timeout=30) as r:
    spec = json.load(r)
OUT.write_text(json.dumps(spec, indent=1))

print('spec version:', spec.get('info', {}).get('version'))
print('servers:', [s.get('url') for s in spec.get('servers', [])])
print('\nendpoints:')
for p, methods in spec.get('paths', {}).items():
    for m in methods:
        print(f'  {m.upper():<7} {p}')

# order body schema: find the POST /order request schema and enumerate fields
def resolve(ref):
    node = spec
    for part in ref.lstrip('#/').split('/'):
        node = node[part]
    return node


def schema_fields(schema, prefix='', depth=0):
    if depth > 3 or not isinstance(schema, dict):
        return
    if '$ref' in schema:
        yield from schema_fields(resolve(schema['$ref']), prefix, depth)
        return
    for combiner in ('oneOf', 'anyOf', 'allOf'):
        for sub in schema.get(combiner, []):
            yield from schema_fields(sub, prefix, depth)
    for name, sub in (schema.get('properties') or {}).items():
        t = sub.get('type', sub.get('$ref', '?'))
        enum = sub.get('enum')
        req = name in (schema.get('required') or [])
        yield f"{prefix}{name}: {t}{' enum=' + str(enum) if enum else ''}{' REQUIRED' if req else ''}"
        if sub.get('type') == 'object' or '$ref' in sub or sub.get('oneOf') or sub.get('anyOf'):
            yield from schema_fields(sub, prefix + name + '.', depth + 1)


print('\nPOST /order request schema:')
try:
    body = spec['paths']['/v1/order']['post']['requestBody']['content']['application/json']['schema']
    seen = set()
    for line in schema_fields(body):
        if line not in seen:
            seen.add(line)
            print('  ', line)
except KeyError as e:
    print('  could not resolve:', e)

print('\nPOST /quote request schema:')
try:
    body = spec['paths']['/v1/quote']['post']['requestBody']['content']['application/json']['schema']
    seen = set()
    for line in schema_fields(body):
        if line not in seen:
            seen.add(line)
            print('  ', line)
except KeyError as e:
    print('  could not resolve:', e)

# our client's endpoint usage
print('\n--- our client endpoint strings (trading/execution.py) ---')
src = Path('trading/execution.py').read_text()
for m in sorted(set(re.findall(r'["\'](/?(?:v2/)?(?:flash/)?(?:quote|order|orders|trade|balance|asset)[a-z/_{}-]*)["\']', src, re.I))):
    print('  ', m)
urls = sorted(set(re.findall(r'https://[a-z0-9./_-]+', src)))
for u in urls:
    print('  URL:', u)
