"""Private snapshot parser subprocess. Input and output paths are local only."""
from dataclasses import asdict
import json
from pathlib import Path
import resource
import sys

from mycelos.knowledge.local_readers import read_units


def main():
    request = json.loads(sys.stdin.buffer.read(65536))
    # A parser failure never leaves an unbounded reader in the collector process.
    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
    resource.setrlimit(resource.RLIMIT_FSIZE, (request['max_bytes'], request['max_bytes']))
    try:
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    except (ValueError, OSError):
        # Some macOS versions do not implement this resource limit.
        pass
    root = Path(request['root'])
    total = 0
    seen = set()
    try:
        with open(request['output'], 'x', encoding='utf-8') as stream:
            for unit in read_units(request['kind'], root, request['workflow']):
                if len(seen) >= request['max_units'] or unit.external_id in seen:
                    raise ValueError('Unit count limit or duplicate ID')
                seen.add(unit.external_id)
                line = json.dumps(asdict(unit)) + '\n'
                total += len(line.encode())
                if total > request['max_bytes']:
                    raise ValueError('Unit byte limit exceeded')
                stream.write(line)
    except Exception:
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
