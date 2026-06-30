"""Zone-discipline guard scenarios (method-level, no runner construction)."""
import sys
import types

sys.path.insert(0, '.')

from discovery.live_runner import LiveRunner

guard = LiveRunner._zone_discipline_block_reason

NOW = 1_800_000_000.0


def fake(zone=None, alert_until=0.0, enabled=True, tol=0.03):
    return types.SimpleNamespace(
        zone_discipline=enabled,
        zone_tolerance=tol,
        alert_until={'TOK': alert_until},
        alert_zone={'TOK': zone} if zone else {},
    )


ZONE = {'lo': 17800.0, 'hi': 18900.0, 'at': NOW - 600}
LIVE = NOW + 3600  # cooldown still active

cases = [
    # (description, self, price, expect_blocked_substring or '')
    ('no live signal -> allowed', fake(ZONE, alert_until=NOW - 1), 15000.0, ''),
    ('below zone while live -> blocked', fake(ZONE, LIVE), 15000.0, 'below called zone'),
    ('within 3% tolerance of lo -> allowed', fake(ZONE, LIVE), 17500.0, ''),
    ('inside zone -> allowed', fake(ZONE, LIVE), 18000.0, ''),
    ('above zone hi -> blocked', fake(ZONE, LIVE), 19500.0, 'above called zone'),
    ('live cooldown but no recorded zone -> allowed', fake(None, LIVE), 15000.0, ''),
    ('disabled flag -> allowed', fake(ZONE, LIVE, enabled=False), 15000.0, ''),
    ('exactly at tolerance floor -> allowed', fake(ZONE, LIVE), 17800.0 * 0.97 + 0.01, ''),
]

fails = []
for desc, self_, price, expect in cases:
    got = guard(self_, 'TOK', price, NOW)
    ok = (expect in got) if expect else (got == '')
    print(f'{"PASS" if ok else "FAIL"}  {desc}  -> {got!r}')
    if not ok:
        fails.append(desc)

print('\nZONE DISCIPLINE TESTS ' + ('FAIL' if fails else 'PASS'))
sys.exit(1 if fails else 0)
