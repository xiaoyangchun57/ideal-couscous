"""Allow deployment registration only for an explicitly listed candidate tag."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


DEFAULT_MANIFEST = Path(__file__).with_name('release-candidates.json')


def _load_manifest(path: Path) -> dict:
    try:
        manifest = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'cannot read candidate manifest {path}: {exc}') from exc
    if not isinstance(manifest, dict):
        raise ValueError('candidate manifest must be a JSON object')
    candidates = manifest.get('candidate_tags')
    historical = manifest.get('historical_tags')
    if (not isinstance(candidates, list) or not candidates
            or any(not isinstance(tag, str) or not tag for tag in candidates)):
        raise ValueError('candidate_tags must be a non-empty list of tag strings')
    if not isinstance(historical, list) or any(not isinstance(tag, str) or not tag for tag in historical):
        raise ValueError('historical_tags must be a list of tag strings')
    if set(candidates) & set(historical):
        raise ValueError('candidate_tags and historical_tags must be disjoint')
    return manifest


def verify_candidate(tag: str, manifest_path: Path = DEFAULT_MANIFEST) -> int:
    try:
        manifest = _load_manifest(manifest_path)
    except ValueError as exc:
        print(f'REJECT candidate manifest: {exc}')
        return 2

    if tag in manifest['historical_tags']:
        print(f'REJECT historical release tag: {tag}')
        return 1
    if tag not in manifest['candidate_tags']:
        print(f'REJECT tag not listed as a current candidate: {tag}')
        return 1
    print(f'ACCEPT current release candidate: {tag}')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True, help='tag to register or deploy')
    parser.add_argument('--manifest', type=Path, default=DEFAULT_MANIFEST,
                        help='candidate manifest JSON path')
    args = parser.parse_args(argv)
    return verify_candidate(args.tag, args.manifest)


if __name__ == '__main__':
    raise SystemExit(main())
