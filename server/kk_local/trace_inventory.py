"""Read an existing trace into a payload-free message inventory (no new ledger)."""
import argparse
from collections import Counter
import json
from pathlib import Path


def inventory(paths, transport):
    counts = Counter()
    invalid = 0
    for path in paths:
        with Path(path).open(encoding='utf-8-sig') as source:
            for line in source:
                try:
                    row = json.loads(line)
                    if row.get('event') not in ('rx', 'tx') or 'id' not in row:
                        continue
                    ident = int(row['id'])
                    size = int(row.get('size', row.get('bytes', -1)))
                    if ident < 0 or size < 0:
                        raise ValueError('invalid ID/size')
                    role = str(row.get('phase', row.get('role', 'unspecified')))
                    counts[(row['event'], role, ident, size)] += 1
                except (ValueError, TypeError, KeyError):
                    invalid += 1
    return dict(schema='kk-message-inventory-v1', transport=transport,
                semantic_completion_claim=False, invalid_lines=invalid,
                messages=[dict(direction=k[0], phase_or_role=k[1], id=k[2],
                               payload_size=k[3], count=n) for k, n in sorted(counts.items())])


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--transport', choices=('game-tcp', 'sdlogin-tcp', 'sdp2p-udp'), required=True)
    p.add_argument('paths', nargs='+')
    args = p.parse_args()
    print(json.dumps(inventory(args.paths, args.transport), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
