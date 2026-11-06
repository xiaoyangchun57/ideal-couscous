"""Aggregate JavaScript and Python syntax checks for the handoff gate."""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from pathlib import Path


def _display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(path.resolve())


def _source_files(root: Path, suffix: str) -> list[Path]:
    return sorted(path for path in root.rglob(f'*{suffix}') if path.is_file())


def _run_check(label: str, command: list[str], path: Path) -> bool:
    result = subprocess.run(command + [str(path)], capture_output=True, text=True,
                            encoding='utf-8', errors='replace')
    display = _display_path(path)
    if result.returncode == 0:
        print(f'PASS {label}: {display}')
        return True

    print(f'FAIL {label}: {display} (exit {result.returncode})')
    output = '\n'.join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
    if output:
        print(output)
    return False


def run_checks(node_root: Path | None, python_root: Path | None) -> int:
    failures = 0
    checked = 0

    if node_root is not None:
        node = shutil.which('node')
        if not node:
            print('FAIL node --check: node executable was not found')
            failures += 1
        else:
            for path in _source_files(node_root, '.js'):
                checked += 1
                failures += not _run_check('node --check', [node, '--check'], path)

    if python_root is not None:
        for path in _source_files(python_root, '.py'):
            checked += 1
            failures += not _run_check('py_compile', [sys.executable, '-m', 'py_compile'], path)

    if failures:
        print(f'SYNTAX CHECK FAILED: {failures} failure(s) across {checked} file(s)')
        return 1

    print(f'SYNTAX CHECK PASSED: {checked} file(s)')
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--node-root', type=Path, help='root directory containing JavaScript files')
    parser.add_argument('--python-root', type=Path, help='root directory containing Python files')
    args = parser.parse_args(argv)
    if args.node_root is None and args.python_root is None:
        parser.error('at least one of --node-root or --python-root is required')

    for option, root in (('--node-root', args.node_root), ('--python-root', args.python_root)):
        if root is not None and not root.is_dir():
            parser.error(f'{option} is not a directory: {root}')

    return run_checks(args.node_root, args.python_root)


if __name__ == '__main__':
    raise SystemExit(main())
