"""Dump Flash spec enums, quote response schema, and order-status values."""
import json

spec = json.load(open('analysis/_flash_openapi.json'))
comp = spec.get('components', {}).get('schemas', {})

for name, schema in comp.items():
    if schema.get('enum'):
        print(f'{name}: enum={schema["enum"]}')

print('\n--- schema names ---')
print(sorted(comp.keys()))


def resolve(node):
    while isinstance(node, dict) and '$ref' in node:
        ref = node['$ref'].split('/')[-1]
        node = comp[ref]
    return node


def walk(schema, prefix='', depth=0):
    schema = resolve(schema)
    if depth > 4 or not isinstance(schema, dict):
        return
    for c in ('oneOf', 'anyOf', 'allOf'):
        for sub in schema.get(c, []):
            walk(sub, prefix, depth)
    for n, sub in (schema.get('properties') or {}).items():
        s = resolve(sub)
        t = s.get('type', '?')
        e = f" enum={s['enum']}" if s.get('enum') else ''
        print(f'  {prefix}{n}: {t}{e}')
        if t == 'object' or s.get('properties'):
            walk(s, prefix + n + '.', depth + 1)


print('\n--- POST /quote 200 response ---')
resp = spec['paths']['/v1/quote']['post']['responses']
ok = resp.get('200') or resp.get('201')
walk(ok['content']['application/json']['schema'])

print('\n--- POST /order 200/201 response ---')
resp = spec['paths']['/v1/order']['post']['responses']
ok = resp.get('200') or resp.get('201')
walk(ok['content']['application/json']['schema'])
