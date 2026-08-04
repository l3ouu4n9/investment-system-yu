# MMI H2c Prospective Dual-Side Capture Runbook

## 1. Purpose and authority boundary

This runbook is the operator procedure for one explicitly invoked, foreground,
report-only H2c prospective capture. It exposes the new H1 prompt and the
current Step 1 / Deep Research prompt, waits for two response files supplied by
the operator, and persists a portable case bundle before the comparison report
and receipt.

The procedure has `authority effect = NONE`. It does not change `HOLD`,
`NO_TRADE`, `SELL`, `NEW_BUY`, `ORDER_COMPILATION`, legacy Step 1, weekly
routing, Steps 2–4, availability, permissions, gates, final safety,
publication, pointers, orders, or brokers.

Nothing in this procedure selects or authenticates a provider, calls a model
API, submits a prompt, retrieves a response, polls, retries, schedules work,
runs in the background, publishes an artifact, or grants replacement or
investment authority.

## 2. Required Linux/GNU environment

This procedure is designed and validated only for Linux with Bash, GNU
coreutils, GNU findutils, and the repository-local Python virtual environment.
It specifically relies on GNU `dd` with `iflag=fullblock,nofollow` for each
source archive input, and on GNU `stat` during private-directory creation. It
also relies on Linux `O_NOFOLLOW`, `O_DIRECTORY`, `O_CLOEXEC`,
descriptor-relative opens, descriptor `fsync`/`fchmod`, and `/dev/fd`. GNU
`tee`, `sync`, `find`, `sort`, `sha256sum`, `cmp`, and `wc` are named here to
make the boundary explicit: the corrected executable blocks do not rely on
them for retained-path writes, stream capture, hashing, comparison, inventory
production, or permission finalization, and use no process substitution.

This is not a POSIX, macOS, BSD, or cross-platform procedure. Do not install,
sync, or download packages while preparing or running a case. The committed
repository-local `.venv/bin/python` is the only Python entry point used here.

## 3. Case identity and durable layout

Store every attempt outside the repository under:

```text
$HOME/investment-system-evidence/h2c/<CASE_ID>/
```

The operator authors a fresh case ID with this form:

```text
YYYYMMDDTHHMMSSZ-9488d389-attemptNN
```

For example, `20260803T231530Z-9488d389-attempt01` contains an
operator-recorded UTC time, the pinned repository short SHA, and an attempt
discriminator. The ID is descriptive metadata only. Deterministic code does
not consume it, and it is not an authority signal.

Every failed, cancelled, interrupted, or validation-failed attempt permanently
consumes its case ID. A later attempt always uses a new case ID and new absent
session leaves.

The complete retained layout is:

```text
<CASE_ID>/
  prompts/
    h1_prompt.txt
    legacy_prompt.txt
  responses/
    h1_response.raw
    legacy_response.raw
  artifacts/
    case_evidence_bundle.json
    comparison_report.json
    receipt.json
  archive/
    strategy_settings.yaml
    portfolio_snapshot.txt
    research_dual_lane.txt
  meta/
    repo_commit.txt
    expected_sha256.txt
    sha256_inventory.txt
    cli_stdout.txt
    cli_stderr.txt
    operator_notes.md
```

Only the two prompt leaves, two response leaves, and three artifact leaves are
session path arguments. Files under `archive/` and `meta/` are operator
evidence and are not deterministic session inputs.

Mutable preparation material is kept in a private, operator-owned staging root
outside both the case root and `/tmp`. Retained metadata is copied from that
staging root exactly once into an absent case leaf; retained case paths are
never reopened for append or replacement.

`operator_notes.md` is always:

```text
operator-authored
non-authoritative
not consumed by deterministic code
```

It must never contain credentials, passwords, cookies, provider session data,
or claims of authenticated provider origin.

## 4. Repository preflight

Start a new Bash session. Replace only the explicit repository path. Do not
pull, reset, clean, checkout, merge, stash, or otherwise change the repository
to make this preflight pass.

```bash
set -euo pipefail
umask 077

H2C_REPOSITORY_ROOT=/absolute/path/to/investment-system-yu
H2C_EXPECTED_HEAD=9488d389d00a4e21a5842c4fa59aa95ff7af1d09
H2C_EXPECTED_PARENT=e9632f4906ff81e3110f76739f8f4a085aea5e6c
H2C_EXPECTED_SUBJECT='feat(mmi): persist H2c case bundle during capture'

[[ "$H2C_REPOSITORY_ROOT" = /* ]]
cd -- "$H2C_REPOSITORY_ROOT"
[[ "$(pwd -P)" = "$H2C_REPOSITORY_ROOT" ]]
[[ "$(git rev-parse --show-toplevel)" = "$H2C_REPOSITORY_ROOT" ]]
[[ "$(git rev-parse HEAD)" = "$H2C_EXPECTED_HEAD" ]]
[[ "$(git rev-parse HEAD^)" = "$H2C_EXPECTED_PARENT" ]]
[[ "$(git show -s --format=%s HEAD)" = "$H2C_EXPECTED_SUBJECT" ]]
git diff --quiet --
git diff --cached --quiet --
git diff --check
[[ -z "$(git ls-files --others --exclude-standard)" ]]
```

Any failure aborts before case-root creation.

## 5. Exclusive case and staging-directory creation

Continue in the same Bash session. Replace the UTC value with the current UTC
time recorded by the operator and select the next never-used attempt number.

```bash
H2C_CASE_UTC=20260803T231530Z
H2C_ATTEMPT=attempt01
[[ "$H2C_CASE_UTC" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
[[ "$H2C_ATTEMPT" =~ ^attempt[0-9]{2,}$ ]]

H2C_CASE_ID="${H2C_CASE_UTC}-9488d389-${H2C_ATTEMPT}"
[[ "$H2C_CASE_ID" =~ ^[0-9]{8}T[0-9]{6}Z-9488d389-attempt[0-9]{2,}$ ]]

[[ -n "${HOME:-}" && "$HOME" = /* ]]
[[ -d "$HOME" && ! -L "$HOME" ]]

H2C_EVIDENCE_PARENT="$HOME/investment-system-evidence"
H2C_EVIDENCE_ROOT="$H2C_EVIDENCE_PARENT/h2c"
H2C_STAGING_PARENT="$H2C_EVIDENCE_PARENT/h2c-staging"
H2C_CASE_ROOT="$H2C_EVIDENCE_ROOT/$H2C_CASE_ID"
H2C_STAGING_ROOT="$H2C_STAGING_PARENT/$H2C_CASE_ID"

[[ "$H2C_CASE_ROOT" != /tmp && "$H2C_CASE_ROOT" != /tmp/* ]]
[[ "$H2C_STAGING_ROOT" != /tmp && "$H2C_STAGING_ROOT" != /tmp/* ]]
[[ "$H2C_CASE_ROOT" != "$H2C_REPOSITORY_ROOT" ]]
[[ "$H2C_CASE_ROOT" != "$H2C_REPOSITORY_ROOT/"* ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT" ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT/"* ]]

for directory in \
  "$H2C_EVIDENCE_PARENT" \
  "$H2C_EVIDENCE_ROOT" \
  "$H2C_STAGING_PARENT"; do
  if [[ -e "$directory" || -L "$directory" ]]; then
    [[ -d "$directory" && ! -L "$directory" ]]
  else
    mkdir -m 700 -- "$directory"
  fi
done

[[ ! -e "$H2C_CASE_ROOT" && ! -L "$H2C_CASE_ROOT" ]]
[[ ! -e "$H2C_STAGING_ROOT" && ! -L "$H2C_STAGING_ROOT" ]]
mkdir -m 700 -- "$H2C_CASE_ROOT"
mkdir -m 700 -- "$H2C_STAGING_ROOT"
mkdir -m 700 -- \
  "$H2C_CASE_ROOT/prompts" \
  "$H2C_CASE_ROOT/responses" \
  "$H2C_CASE_ROOT/artifacts" \
  "$H2C_CASE_ROOT/archive" \
  "$H2C_CASE_ROOT/meta" \
  "$H2C_STAGING_ROOT/responses" \
  "$H2C_STAGING_ROOT/meta"

for directory in \
  "$H2C_STAGING_PARENT" \
  "$H2C_STAGING_ROOT" \
  "$H2C_STAGING_ROOT/responses" \
  "$H2C_STAGING_ROOT/meta"; do
  [[ -d "$directory" && ! -L "$directory" && -O "$directory" ]]
  [[ "$(stat -c '%a' -- "$directory")" = 700 ]]
done

H2C_H1_PROMPT="$H2C_CASE_ROOT/prompts/h1_prompt.txt"
H2C_LEGACY_PROMPT="$H2C_CASE_ROOT/prompts/legacy_prompt.txt"
H2C_H1_RESPONSE="$H2C_CASE_ROOT/responses/h1_response.raw"
H2C_LEGACY_RESPONSE="$H2C_CASE_ROOT/responses/legacy_response.raw"
H2C_CASE_BUNDLE="$H2C_CASE_ROOT/artifacts/case_evidence_bundle.json"
H2C_COMPARISON_REPORT="$H2C_CASE_ROOT/artifacts/comparison_report.json"
H2C_RECEIPT="$H2C_CASE_ROOT/artifacts/receipt.json"

H2C_SESSION_LEAVES=(
  "$H2C_H1_PROMPT"
  "$H2C_LEGACY_PROMPT"
  "$H2C_H1_RESPONSE"
  "$H2C_LEGACY_RESPONSE"
  "$H2C_CASE_BUNDLE"
  "$H2C_COMPARISON_REPORT"
  "$H2C_RECEIPT"
)

for leaf in "${H2C_SESSION_LEAVES[@]}"; do
  [[ "$leaf" = /* ]]
  [[ -d "${leaf%/*}" && ! -L "${leaf%/*}" ]]
  [[ ! -e "$leaf" && ! -L "$leaf" ]]
done
```

Do not use `/tmp`, any repository-local artifact directory, a default filename,
a case-directory discovery rule, or a `latest` symlink or pointer. The staging
root is private, operator-owned, non-symlinked, outside the case root, and
permanently associated with the same consumed attempt ID. Do not pre-create
empty response files or any other session leaf.

## 6. Archive the exact source bytes

The production capture reads exactly:

```text
inputs/current/strategy_settings.yaml
inputs/current/portfolio_snapshot.txt
prompts/research_dual_lane.txt
```

Continue in the same Bash session. The documentation-only standard-library
archiver below opens the repository, case, staging, and every intervening
directory component with `O_DIRECTORY | O_NOFOLLOW`. It opens each fixed
source leaf with `O_NOFOLLOW`, creates every archive or metadata leaf relative
to a verified directory descriptor with `O_EXCL | O_NOFOLLOW`, compares the
exact bytes through descriptors, and fsyncs every new file and owning
directory. It neither parses nor reserializes source bytes.

```bash
H2C_ARCHIVE_UTC=20260803T231700Z
[[ "$H2C_ARCHIVE_UTC" =~ ^[0-9]{8}T[0-9]{6}Z$ ]]
H2C_ARCHIVE_RECORD="$(
  PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python /dev/fd/3 \
    "$H2C_REPOSITORY_ROOT" \
    "$H2C_CASE_ROOT" \
    "$H2C_STAGING_ROOT" \
    "$H2C_EXPECTED_HEAD" \
    "$H2C_EXPECTED_PARENT" \
    "$H2C_EXPECTED_SUBJECT" \
    "$H2C_ARCHIVE_UTC" 3<<'PY'
from __future__ import annotations

import errno
import hashlib
import os
import stat
import subprocess
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_SOURCE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW_FILE_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | os.O_NOFOLLOW
    | os.O_CLOEXEC
)


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "directory must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise OSError(errno.EINVAL, "directory path is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short metadata write")
        view = view[written:]


def _witness(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_source_with_gnu_dd(directory_fd: int, name: str) -> bytes:
    descriptor = os.open(name, _SOURCE_FLAGS, dir_fd=directory_fd)
    saved_cwd_fd = os.open(".", _DIRECTORY_FLAGS)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "source is not regular")
        direct_chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            direct_chunks.append(chunk)
        os.fchdir(directory_fd)
        try:
            completed = subprocess.run(
                [
                    "dd",
                    f"if={name}",
                    "bs=65536",
                    "iflag=fullblock,nofollow",
                    "status=none",
                ],
                check=False,
                stdout=subprocess.PIPE,
            )
        finally:
            os.fchdir(saved_cwd_fd)
        if completed.returncode != 0:
            raise OSError(errno.EIO, "GNU dd source read failed")
        after = os.fstat(descriptor)
        direct_value = b"".join(direct_chunks)
        if (
            _witness(before) != _witness(after)
            or len(direct_value) != before.st_size
            or completed.stdout != direct_value
        ):
            raise OSError(errno.EBUSY, "source changed while archived")
        return completed.stdout
    finally:
        os.close(saved_cwd_fd)
        os.close(descriptor)


def _write_new(directory_fd: int, name: str, value: bytes) -> bytes:
    descriptor = os.open(
        name,
        _NEW_FILE_FLAGS,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or stat.S_IMODE(created.st_mode) != 0o600
        ):
            raise OSError(errno.EPERM, "new evidence leaf is not private")
        _write_all(descriptor, value)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        persisted = b"".join(chunks)
        if persisted != value:
            raise OSError(errno.EIO, "new evidence comparison failed")
        return persisted
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 8:
        raise OSError(errno.EINVAL, "seven archive arguments required")
    repository_root, case_root, staging_root = sys.argv[1:4]
    expected_head, expected_parent, expected_subject, archive_utc = sys.argv[4:]
    repository_fd = _open_absolute_directory(repository_root)
    case_fd = _open_absolute_directory(case_root)
    staging_fd = _open_absolute_directory(staging_root)
    descriptors: list[int] = []
    try:
        staging_stat = os.fstat(staging_fd)
        if (
            staging_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging root is not private")
        inputs_fd = os.open("inputs", _DIRECTORY_FLAGS, dir_fd=repository_fd)
        descriptors.append(inputs_fd)
        current_fd = os.open("current", _DIRECTORY_FLAGS, dir_fd=inputs_fd)
        descriptors.append(current_fd)
        prompts_fd = os.open("prompts", _DIRECTORY_FLAGS, dir_fd=repository_fd)
        descriptors.append(prompts_fd)
        archive_fd = os.open("archive", _DIRECTORY_FLAGS, dir_fd=case_fd)
        descriptors.append(archive_fd)
        case_meta_fd = os.open("meta", _DIRECTORY_FLAGS, dir_fd=case_fd)
        descriptors.append(case_meta_fd)
        staging_meta_fd = os.open("meta", _DIRECTORY_FLAGS, dir_fd=staging_fd)
        descriptors.append(staging_meta_fd)
        staging_meta_stat = os.fstat(staging_meta_fd)
        if (
            staging_meta_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_meta_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging metadata is not private")

        settings = _read_source_with_gnu_dd(
            current_fd, "strategy_settings.yaml"
        )
        portfolio = _read_source_with_gnu_dd(
            current_fd, "portfolio_snapshot.txt"
        )
        template = _read_source_with_gnu_dd(
            prompts_fd, "research_dual_lane.txt"
        )
        settings_archive = _write_new(
            archive_fd, "strategy_settings.yaml", settings
        )
        portfolio_archive = _write_new(
            archive_fd, "portfolio_snapshot.txt", portfolio
        )
        template_archive = _write_new(
            archive_fd, "research_dual_lane.txt", template
        )
        settings_sha = hashlib.sha256(settings_archive).hexdigest()
        portfolio_sha = hashlib.sha256(portfolio_archive).hexdigest()
        template_sha = hashlib.sha256(template_archive).hexdigest()
        repo_metadata = (
            f"head={expected_head}\n"
            f"parent={expected_parent}\n"
            f"subject={expected_subject}\n"
            f"archive_utc={archive_utc}\n"
        ).encode()
        hash_metadata = (
            "inputs/current/strategy_settings.yaml\t"
            f"archive/strategy_settings.yaml\t{len(settings_archive)}\t"
            f"{settings_sha}\n"
            "inputs/current/portfolio_snapshot.txt\t"
            f"archive/portfolio_snapshot.txt\t{len(portfolio_archive)}\t"
            f"{portfolio_sha}\n"
            "prompts/research_dual_lane.txt\t"
            f"archive/research_dual_lane.txt\t{len(template_archive)}\t"
            f"{template_sha}\n"
        ).encode()
        notes_header = (
            b"# operator-authored\n"
            b"# non-authoritative\n"
            b"# not consumed by deterministic code\n"
        )
        _write_new(case_meta_fd, "repo_commit.txt", repo_metadata)
        _write_new(case_meta_fd, "expected_sha256.txt", hash_metadata)
        _write_new(
            staging_meta_fd,
            "operator_notes.md.staging",
            notes_header,
        )
        os.fsync(archive_fd)
        os.fsync(case_meta_fd)
        os.fsync(staging_meta_fd)
        print(
            "\t".join(
                (
                    settings_sha,
                    str(len(settings_archive)),
                    portfolio_sha,
                    str(len(portfolio_archive)),
                    template_sha,
                    str(len(template_archive)),
                )
            )
        )
        return 0
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        os.close(staging_fd)
        os.close(case_fd)
        os.close(repository_fd)


raise SystemExit(main())
PY
)"

IFS=$'\t' read -r \
  H2C_SETTINGS_SHA256 H2C_SETTINGS_BYTES \
  H2C_PORTFOLIO_SHA256 H2C_PORTFOLIO_BYTES \
  H2C_TEMPLATE_SHA256 H2C_TEMPLATE_BYTES \
  <<< "$H2C_ARCHIVE_RECORD"
for digest in \
  "$H2C_SETTINGS_SHA256" \
  "$H2C_PORTFOLIO_SHA256" \
  "$H2C_TEMPLATE_SHA256"; do
  [[ "$digest" =~ ^[0-9a-f]{64}$ ]]
done
for byte_count in \
  "$H2C_SETTINGS_BYTES" \
  "$H2C_PORTFOLIO_BYTES" \
  "$H2C_TEMPLATE_BYTES"; do
  [[ "$byte_count" =~ ^[0-9]+$ ]]
done
```

`repo_commit.txt` and `expected_sha256.txt` are complete when their absent
retained leaves are opened once. `operator_notes.md` remains absent in the case
until its private staging copy is complete and finalized later. No retained
metadata pathname is reopened for append.

The settings and portfolio hashes above, derived from the archives, are the
two expected hashes supplied to the CLI. Do not copy inspection-time hashes
from this runbook or another case.

## 7. Immediate pre-invocation recheck

Immediately before invoking the session, repeat the repository checks and
compare every live source to its archived bytes and hash through verified
directory descriptors. Any failure consumes the case ID; retain the case
unchanged and stop.

```bash
[[ "$(git rev-parse HEAD)" = "$H2C_EXPECTED_HEAD" ]]
[[ "$(git rev-parse HEAD^)" = "$H2C_EXPECTED_PARENT" ]]
[[ "$(git show -s --format=%s HEAD)" = "$H2C_EXPECTED_SUBJECT" ]]
git diff --quiet --
git diff --cached --quiet --
git diff --check
[[ -z "$(git ls-files --others --exclude-standard)" ]]

PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python /dev/fd/3 \
  "$H2C_REPOSITORY_ROOT" \
  "$H2C_CASE_ROOT" \
  "$H2C_SETTINGS_SHA256" \
  "$H2C_PORTFOLIO_SHA256" \
  "$H2C_TEMPLATE_SHA256" 3<<'PY'
from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "directory must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise OSError(errno.EINVAL, "directory path is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _read(directory_fd: int, name: str) -> bytes:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "source or archive is not regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        value = b"".join(chunks)
        if (
            before.st_dev,
            before.st_ino,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        ) != (
            after.st_dev,
            after.st_ino,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        ) or len(value) != before.st_size:
            raise OSError(errno.EBUSY, "source or archive changed")
        return value
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 6:
        raise OSError(errno.EINVAL, "five recheck arguments required")
    repository_root, case_root = sys.argv[1:3]
    expected_hashes = sys.argv[3:]
    repository_fd = _open_absolute_directory(repository_root)
    case_fd = _open_absolute_directory(case_root)
    descriptors: list[int] = []
    try:
        inputs_fd = os.open("inputs", _DIRECTORY_FLAGS, dir_fd=repository_fd)
        descriptors.append(inputs_fd)
        current_fd = os.open("current", _DIRECTORY_FLAGS, dir_fd=inputs_fd)
        descriptors.append(current_fd)
        prompts_fd = os.open("prompts", _DIRECTORY_FLAGS, dir_fd=repository_fd)
        descriptors.append(prompts_fd)
        archive_fd = os.open("archive", _DIRECTORY_FLAGS, dir_fd=case_fd)
        descriptors.append(archive_fd)
        pairs = (
            (current_fd, "strategy_settings.yaml", "strategy_settings.yaml"),
            (current_fd, "portfolio_snapshot.txt", "portfolio_snapshot.txt"),
            (prompts_fd, "research_dual_lane.txt", "research_dual_lane.txt"),
        )
        for (source_fd, source_name, archive_name), expected in zip(
            pairs, expected_hashes, strict=True
        ):
            source = _read(source_fd, source_name)
            archive = _read(archive_fd, archive_name)
            if source != archive or hashlib.sha256(archive).hexdigest() != expected:
                raise OSError(errno.EIO, "live source and archive differ")
        return 0
    finally:
        for descriptor in reversed(descriptors):
            os.close(descriptor)
        os.close(case_fd)
        os.close(repository_fd)


raise SystemExit(main())
PY

for leaf in "${H2C_SESSION_LEAVES[@]}"; do
  [[ -d "${leaf%/*}" && ! -L "${leaf%/*}" ]]
  [[ ! -e "$leaf" && ! -L "$leaf" ]]
done
```

## 8. Exact foreground invocation and stream capture

The invocation has exactly two expected SHA options and seven absolute,
role-specific path options. It runs through the existing repository
environment, remains in the foreground, and retains interactive terminal
stdin.

The documentation-only standard-library launcher below is used because shell
redirection and `tee -a` would reopen retained log pathnames. The launcher
walks the case root without following directory symlinks, opens each absent log
exactly once with `O_EXCL | O_NOFOLLOW | O_CLOEXEC`, gives the CLI the retained
stdout descriptor, and copies each unmodified stderr byte both to the retained
stderr descriptor and terminal descriptor 2. It fsyncs both logs and their
parent directory before reporting the exact CLI status. Launcher status and
CLI status remain separate.

```bash
H2C_CLI_STDOUT="$H2C_CASE_ROOT/meta/cli_stdout.txt"
H2C_CLI_STDERR="$H2C_CASE_ROOT/meta/cli_stderr.txt"
[[ ! -e "$H2C_CLI_STDOUT" && ! -L "$H2C_CLI_STDOUT" ]]
[[ ! -e "$H2C_CLI_STDERR" && ! -L "$H2C_CLI_STDERR" ]]
[[ -x "$H2C_REPOSITORY_ROOT/.venv/bin/python" ]]

set +e
H2C_STATUS_RECORD="$(
  PYTHONDONTWRITEBYTECODE=1 \
  .venv/bin/python /dev/fd/3 \
    "$H2C_REPOSITORY_ROOT" \
    "$H2C_CASE_ROOT" \
    "$H2C_SETTINGS_SHA256" \
    "$H2C_PORTFOLIO_SHA256" \
    "$H2C_H1_PROMPT" \
    "$H2C_LEGACY_PROMPT" \
    "$H2C_H1_RESPONSE" \
    "$H2C_LEGACY_RESPONSE" \
    "$H2C_CASE_BUNDLE" \
    "$H2C_COMPARISON_REPORT" \
    "$H2C_RECEIPT" 3<<'PY'
from __future__ import annotations

import errno
import os
import signal
import stat
import subprocess
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_NEW_FILE_FLAGS = (
    os.O_WRONLY
    | os.O_CREAT
    | os.O_EXCL
    | os.O_NOFOLLOW
    | os.O_CLOEXEC
)


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "directory must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise OSError(errno.EINVAL, "directory path is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _open_new_regular(directory_fd: int, name: str) -> int:
    descriptor = os.open(
        name,
        _NEW_FILE_FLAGS,
        0o600,
        dir_fd=directory_fd,
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor)
        raise OSError(errno.EINVAL, "log is not regular")
    return descriptor


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short stream write")
        view = view[written:]


def main() -> int:
    if len(sys.argv) != 12 or not sys.stdin.isatty():
        raise OSError(errno.ENOTTY, "foreground terminal stdin required")
    (
        repository_root,
        case_root,
        settings_sha256,
        portfolio_sha256,
        h1_prompt,
        legacy_prompt,
        h1_response,
        legacy_response,
        case_bundle,
        comparison_report,
        receipt,
    ) = sys.argv[1:]
    case_fd = _open_absolute_directory(case_root)
    meta_fd = -1
    stdout_fd = -1
    stderr_fd = -1
    try:
        meta_fd = os.open("meta", _DIRECTORY_FLAGS, dir_fd=case_fd)
        stdout_fd = _open_new_regular(meta_fd, "cli_stdout.txt")
        stderr_fd = _open_new_regular(meta_fd, "cli_stderr.txt")
        environment = os.environ.copy()
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        environment["PYTHONPATH"] = "src"
        command = [
            os.path.join(repository_root, ".venv/bin/python"),
            "-m",
            "investment_orchestrator.cli.run_mmi_h2c_capture",
            "--strategy-settings-expected-sha256",
            settings_sha256,
            "--portfolio-snapshot-expected-sha256",
            portfolio_sha256,
            "--h1-prompt-output-path",
            h1_prompt,
            "--legacy-prompt-output-path",
            legacy_prompt,
            "--h1-response-path",
            h1_response,
            "--legacy-response-path",
            legacy_response,
            "--case-evidence-bundle-output-path",
            case_bundle,
            "--comparison-report-output-path",
            comparison_report,
            "--receipt-output-path",
            receipt,
        ]
        previous_sigint = signal.signal(signal.SIGINT, signal.SIG_IGN)
        try:
            process = subprocess.Popen(
                command,
                cwd=repository_root,
                env=environment,
                stdin=None,
                stdout=stdout_fd,
                stderr=subprocess.PIPE,
                close_fds=True,
                preexec_fn=lambda: signal.signal(
                    signal.SIGINT, signal.SIG_DFL
                ),
            )
            assert process.stderr is not None
            try:
                while True:
                    chunk = os.read(process.stderr.fileno(), 65_536)
                    if not chunk:
                        break
                    _write_all(stderr_fd, chunk)
                    _write_all(2, chunk)
            except OSError:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                process.wait()
                raise
            cli_exit = process.wait()
        finally:
            signal.signal(signal.SIGINT, previous_sigint)
        os.fsync(stdout_fd)
        os.fsync(stderr_fd)
        os.fsync(meta_fd)
        print(f"cli_exit={cli_exit}", flush=True)
        return 0
    finally:
        for descriptor in (stderr_fd, stdout_fd, meta_fd, case_fd):
            if descriptor >= 0:
                os.close(descriptor)


raise SystemExit(main())
PY
)"
H2C_CAPTURE_EXIT=$?

if [[ "$H2C_CAPTURE_EXIT" -ne 0 ]]; then
  printf '%s\n' 'H2C stream/log capture failed; case is incomplete.' >&2
  exit 70
fi
if [[ ! "$H2C_STATUS_RECORD" =~ ^cli_exit=(-?[0-9]+)$ ]]; then
  printf '%s\n' 'H2C stream/log status record was malformed.' >&2
  exit 70
fi
H2C_CLI_EXIT="${BASH_REMATCH[1]}"
case "$H2C_CLI_EXIT" in
  0) set -e ;;
  1) printf '%s\n' 'H2C unexpected code failure; stop.' >&2; exit 1 ;;
  2) printf '%s\n' 'H2C argparse usage failure; stop.' >&2; exit 2 ;;
  3) printf '%s\n' 'H2C controlled session failure; stop.' >&2; exit 3 ;;
  *) printf '%s\n' 'H2C abnormal CLI termination; stop.' >&2; exit 1 ;;
esac

PYTHONDONTWRITEBYTECODE=1 \
"$H2C_REPOSITORY_ROOT/.venv/bin/python" /dev/fd/3 \
  "$H2C_CASE_ROOT" 3<<'PY'
from __future__ import annotations

import errno
import os
import re
import stat
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_EXPECTED_FILESYSTEM_ERRNOS = frozenset(
    {
        errno.EACCES,
        errno.EIO,
        errno.ELOOP,
        errno.ENOENT,
        errno.ENOTDIR,
        errno.EPERM,
    }
)
_STDOUT_PATTERN = re.compile(
    rb"comparison_report_identity_sha256=[0-9a-f]{64}\n"
    rb"receipt_identity_sha256=[0-9a-f]{64}\n"
)
_STDOUT_SIZE = (
    len(b"comparison_report_identity_sha256=")
    + 64
    + 1
    + len(b"receipt_identity_sha256=")
    + 64
    + 1
)
_EXPECTED_STDERR = (
    b"H2C prompts are ready; populate both response files, then enter "
    b"H2C_RESPONSES_READY exactly.\n"
)


class _ExpectedLogInputFailure(ValueError):
    pass


def _witness(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise _ExpectedLogInputFailure("case root must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise _ExpectedLogInputFailure("case root is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _read_regular_exact_size(
    directory_fd: int,
    name: str,
    expected_size: int,
) -> bytes:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or before.st_size != expected_size:
            raise _ExpectedLogInputFailure("retained log shape is invalid")
        chunks: list[bytes] = []
        remaining = expected_size + 1
        while remaining:
            chunk = os.read(descriptor, remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        value = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            _witness(before) != _witness(after)
            or len(value) != expected_size
        ):
            raise _ExpectedLogInputFailure("retained log changed while read")
        return value
    finally:
        os.close(descriptor)


def main() -> int:
    if len(sys.argv) != 2:
        raise _ExpectedLogInputFailure("one case-root argument required")
    case_fd = _open_absolute_directory(sys.argv[1])
    meta_fd = -1
    try:
        meta_fd = os.open("meta", _DIRECTORY_FLAGS, dir_fd=case_fd)
        stdout = _read_regular_exact_size(
            meta_fd,
            "cli_stdout.txt",
            _STDOUT_SIZE,
        )
        stderr = _read_regular_exact_size(
            meta_fd,
            "cli_stderr.txt",
            len(_EXPECTED_STDERR),
        )
        if _STDOUT_PATTERN.fullmatch(stdout) is None:
            raise _ExpectedLogInputFailure("retained stdout is invalid")
        if stderr != _EXPECTED_STDERR:
            raise _ExpectedLogInputFailure("retained stderr is invalid")
        print("H2C_CAPTURE_LOGS_VERIFIED")
        return 0
    finally:
        if meta_fd >= 0:
            os.close(meta_fd)
        os.close(case_fd)


def _run() -> int:
    try:
        return main()
    except _ExpectedLogInputFailure:
        return 3
    except OSError as exc:
        if exc.errno in _EXPECTED_FILESYSTEM_ERRNOS:
            return 3
        raise


raise SystemExit(_run())
PY
```

The shell reaches the retained-log verifier only through the CLI `0` branch,
after the launcher has flushed, fsynced, and closed both log descriptors. The
verifier takes exactly the absolute case root, walks it and `meta` through
`O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC`, opens only the two fixed retained log
names through the verified `meta` descriptor with
`O_RDONLY | O_NOFOLLOW | O_CLOEXEC`, requires regular files, and performs no
writes. It prints exactly `H2C_CAPTURE_LOGS_VERIFIED` plus one LF on success.

Verifier exit 0 means both complete retained byte strings match the exact
success contract. Exit 3 is limited to wrong argument arity, an expected
no-follow open/stat/read input failure, a non-regular or changing log, or an
exact stdout/stderr mismatch. Unexpected programming errors retain their
traceback and exit 1. Because the CLI `0` branch restores `set -e` before the
verifier runs, any verifier nonzero stops this block immediately. It must not
be rerun or bypassed.

CLI exits 1, 2, or 3, abnormal CLI termination, malformed launcher status,
launcher/log-capture failure, and retained-log verification failure cannot
reach nominal-success declaration, `CAPTURE_COMPLETE_UNVALIDATED`, portable
validation, operator comparison, final notes, or inventory creation.

Shell exit 70 is reserved by this documentation launcher for stream/log
capture-mechanism failure. It is not a CLI exit code and cannot reach success
processing.

Do not use `&`, `nohup`, a detached terminal, scheduler, watcher, polling loop,
automatic retry wrapper, provider integration, or another entry point.

## 9. Prompt-exposure boundary

After both prompts have been durably exposed, the concrete handoff writes
exactly these stderr bytes, including one trailing LF:

```text
H2C prompts are ready; populate both response files, then enter H2C_RESPONSES_READY exactly.
```

Keep the capture process alive in its foreground terminal. From a second
terminal, perform one explicit check—not a watcher or polling loop. This is an
independently executed block, so it establishes its own fail-closed shell
posture. Set the same absolute case and staging roots first:

```bash
set -euo pipefail
umask 077

H2C_CASE_ROOT=/absolute/path/to/the/existing/case
H2C_STAGING_ROOT=/absolute/path/to/the/private/staging/root

[[ "$H2C_STAGING_ROOT" = /* ]]
[[ "$H2C_STAGING_ROOT" != /tmp && "$H2C_STAGING_ROOT" != /tmp/* ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT" ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT/"* ]]

PYTHONDONTWRITEBYTECODE=1 \
/absolute/path/to/investment-system-yu/.venv/bin/python /dev/fd/3 \
  "$H2C_CASE_ROOT" "$H2C_STAGING_ROOT" 3<<'PY'
from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_NOTES_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "directory must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise OSError(errno.EINVAL, "directory path is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(component, _DIRECTORY_FLAGS, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _read_record(directory_fd: int, name: str, role: str) -> str:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "prompt is not regular")
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
        after = os.fstat(descriptor)
        witness_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        witness_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if witness_before != witness_after or byte_count != before.st_size:
            raise OSError(errno.EBUSY, "prompt changed during inspection")
        return f"prompt\t{role}\t{byte_count}\t{digest.hexdigest()}\n"
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short notes write")
        view = view[written:]


def main() -> int:
    if len(sys.argv) != 3:
        raise OSError(errno.EINVAL, "case and staging roots required")
    case_root, staging_root = sys.argv[1:]
    case_fd = _open_absolute_directory(case_root)
    staging_fd = _open_absolute_directory(staging_root)
    prompt_fd = -1
    staging_meta_fd = -1
    notes_fd = -1
    try:
        staging_stat = os.fstat(staging_fd)
        if (
            staging_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging root is not private")
        prompt_fd = os.open("prompts", _DIRECTORY_FLAGS, dir_fd=case_fd)
        staging_meta_fd = os.open(
            "meta", _DIRECTORY_FLAGS, dir_fd=staging_fd
        )
        staging_meta_stat = os.fstat(staging_meta_fd)
        if (
            staging_meta_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_meta_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging metadata is not private")
        notes_fd = os.open(
            "operator_notes.md.staging",
            _NOTES_FLAGS,
            dir_fd=staging_meta_fd,
        )
        notes_stat = os.fstat(notes_fd)
        if (
            not stat.S_ISREG(notes_stat.st_mode)
            or notes_stat.st_uid != os.geteuid()
            or stat.S_IMODE(notes_stat.st_mode) != 0o600
        ):
            raise OSError(errno.EPERM, "notes staging is not private")
        records = (
            _read_record(prompt_fd, "h1_prompt.txt", "prompts/h1_prompt.txt")
            + _read_record(
                prompt_fd,
                "legacy_prompt.txt",
                "prompts/legacy_prompt.txt",
            )
        ).encode()
        _write_all(notes_fd, records)
        os.fsync(notes_fd)
        os.fsync(staging_meta_fd)
        return 0
    finally:
        for descriptor in (notes_fd, staging_meta_fd, prompt_fd, staging_fd, case_fd):
            if descriptor >= 0:
                os.close(descriptor)


raise SystemExit(main())
PY
```

Inspect each prompt with a read-only viewer and do not save or modify it. File
existence proves prompt exposure only. It does not prove provider submission,
provider receipt, provider origin, or causality.

## 10. Manual LLM handoff

The two human boundaries are:

```text
H1 prompt:
manually submit through the intended ordinary LLM workflow

legacy prompt:
manually submit through the current Step 1 / Deep Research workflow
```

Use the exact exposed prompt bytes. The repository does not choose a provider,
authenticate provider origin, call an API, submit either prompt, retrieve
either response, poll, or retry. Submission surface and UTC time may be noted
only in the private operator-notes staging file as operator-authored,
non-authoritative metadata.

## 11. Save the operator-supplied response bytes

Prefer a direct export or download when the surface provides one. Save each
completed response first to a closed staging file in a restrictive,
operator-controlled location outside the case root and outside `/tmp`. Stop
all writers, require a regular non-symlink staging file, and copy its exact
bytes once into the declared absent leaf. Use the two fixed staging leaves
below; do not substitute a path elsewhere.

```bash
set -euo pipefail
umask 077

H2C_REPOSITORY_ROOT=/absolute/path/to/investment-system-yu
H2C_CASE_ROOT=/absolute/path/to/the/existing/case
H2C_STAGING_ROOT=/absolute/path/to/the/private/staging/root
H2C_H1_STAGING="$H2C_STAGING_ROOT/responses/h1_response.download"
H2C_LEGACY_STAGING="$H2C_STAGING_ROOT/responses/legacy_response.download"
H2C_H1_RESPONSE="$H2C_CASE_ROOT/responses/h1_response.raw"
H2C_LEGACY_RESPONSE="$H2C_CASE_ROOT/responses/legacy_response.raw"
H2C_OPERATOR_NOTES_STAGING="$H2C_STAGING_ROOT/meta/operator_notes.md.staging"

[[ "$H2C_REPOSITORY_ROOT" = /* ]]
cd -- "$H2C_REPOSITORY_ROOT"
[[ "$(pwd -P)" = "$H2C_REPOSITORY_ROOT" ]]
[[ "$(git rev-parse HEAD)" = 9488d389d00a4e21a5842c4fa59aa95ff7af1d09 ]]
[[ "$H2C_CASE_ROOT" = /* ]]
[[ "$H2C_STAGING_ROOT" = /* ]]
[[ "$H2C_STAGING_ROOT" != /tmp && "$H2C_STAGING_ROOT" != /tmp/* ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT" ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT/"* ]]
[[ "$H2C_H1_STAGING" = "$H2C_STAGING_ROOT/responses/"* ]]
[[ "$H2C_LEGACY_STAGING" = "$H2C_STAGING_ROOT/responses/"* ]]

PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python /dev/fd/3 \
  "$H2C_CASE_ROOT" "$H2C_STAGING_ROOT" 3<<'PY'
from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_SOURCE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_NOTES_FLAGS = os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_CLOEXEC
_DESTINATION_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | os.O_NOFOLLOW
    | os.O_CLOEXEC
)


def _components(path: str) -> list[str]:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "path must be absolute")
    values = [part for part in path.split("/") if part]
    if not values or any(part in {".", ".."} for part in values):
        raise OSError(errno.EINVAL, "path is not lexical")
    return values


def _open_absolute_directory(path: str) -> int:
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in _components(path):
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short response write")
        view = view[written:]


def _witness(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _copy_one(
    *,
    staging_directory_fd: int,
    case_directory_fd: int,
    source_name: str,
    destination_name: str,
    relative_role: str,
) -> str:
    source_fd = os.open(
        source_name,
        _SOURCE_FLAGS,
        dir_fd=staging_directory_fd,
    )
    destination_fd = -1
    try:
        source_before = os.fstat(source_fd)
        if (
            not stat.S_ISREG(source_before.st_mode)
            or source_before.st_uid != os.geteuid()
            or stat.S_IMODE(source_before.st_mode) & 0o077
        ):
            raise OSError(errno.EPERM, "staging response is not private")
        destination_fd = os.open(
            destination_name,
            _DESTINATION_FLAGS,
            0o600,
            dir_fd=case_directory_fd,
        )
        destination_stat = os.fstat(destination_fd)
        if (
            not stat.S_ISREG(destination_stat.st_mode)
            or stat.S_IMODE(destination_stat.st_mode) != 0o600
        ):
            raise OSError(errno.EPERM, "response leaf is not private")
        digest = hashlib.sha256()
        byte_count = 0
        while True:
            chunk = os.read(source_fd, 65_536)
            if not chunk:
                break
            digest.update(chunk)
            byte_count += len(chunk)
            _write_all(destination_fd, chunk)
        os.fsync(destination_fd)
        source_after_write = os.fstat(source_fd)
        if _witness(source_before) != _witness(source_after_write):
            raise OSError(errno.EBUSY, "staging response changed")
        os.lseek(source_fd, 0, os.SEEK_SET)
        os.lseek(destination_fd, 0, os.SEEK_SET)
        while True:
            source_chunk = os.read(source_fd, 65_536)
            destination_chunk = os.read(destination_fd, 65_536)
            if source_chunk != destination_chunk:
                raise OSError(errno.EIO, "response comparison failed")
            if not source_chunk:
                break
        source_after_compare = os.fstat(source_fd)
        if _witness(source_before) != _witness(source_after_compare):
            raise OSError(errno.EBUSY, "staging response changed")
        witness = ":".join(str(part) for part in _witness(source_before))
        return (
            f"response\t{relative_role}\t{byte_count}\t"
            f"{digest.hexdigest()}\t{witness}"
        )
    finally:
        if destination_fd >= 0:
            os.close(destination_fd)
        os.close(source_fd)


def main() -> int:
    if len(sys.argv) != 3:
        raise OSError(errno.EINVAL, "case and staging roots required")
    case_root, staging_root = sys.argv[1:]
    case_components = _components(case_root)
    staging_components = _components(staging_root)
    if (
        staging_components[0] == "tmp"
        or staging_components[: len(case_components)] == case_components
        or case_components[: len(staging_components)] == staging_components
    ):
        raise OSError(errno.EPERM, "unsafe staging root")
    case_fd = _open_absolute_directory(case_root)
    staging_fd = _open_absolute_directory(staging_root)
    case_responses_fd = -1
    staging_responses_fd = -1
    staging_meta_fd = -1
    notes_fd = -1
    try:
        staging_stat = os.fstat(staging_fd)
        if (
            staging_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging root is not private")
        case_responses_fd = os.open(
            "responses", _DIRECTORY_FLAGS, dir_fd=case_fd
        )
        staging_responses_fd = os.open(
            "responses", _DIRECTORY_FLAGS, dir_fd=staging_fd
        )
        staging_responses_stat = os.fstat(staging_responses_fd)
        if (
            staging_responses_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_responses_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging responses are not private")
        staging_meta_fd = os.open(
            "meta", _DIRECTORY_FLAGS, dir_fd=staging_fd
        )
        staging_meta_stat = os.fstat(staging_meta_fd)
        if (
            staging_meta_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_meta_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging metadata is not private")
        notes_fd = os.open(
            "operator_notes.md.staging",
            _NOTES_FLAGS,
            dir_fd=staging_meta_fd,
        )
        notes_stat = os.fstat(notes_fd)
        if (
            not stat.S_ISREG(notes_stat.st_mode)
            or notes_stat.st_uid != os.geteuid()
            or stat.S_IMODE(notes_stat.st_mode) != 0o600
        ):
            raise OSError(errno.EPERM, "notes staging is not private")
        records = [
            _copy_one(
                staging_directory_fd=staging_responses_fd,
                case_directory_fd=case_responses_fd,
                source_name="h1_response.download",
                destination_name="h1_response.raw",
                relative_role="responses/h1_response.raw",
            ),
            _copy_one(
                staging_directory_fd=staging_responses_fd,
                case_directory_fd=case_responses_fd,
                source_name="legacy_response.download",
                destination_name="legacy_response.raw",
                relative_role="responses/legacy_response.raw",
            ),
        ]
        os.fsync(case_responses_fd)
        _write_all(notes_fd, ("\n".join(records) + "\n").encode())
        os.fsync(notes_fd)
        os.fsync(staging_meta_fd)
        return 0
    finally:
        for descriptor in (
            notes_fd,
            staging_meta_fd,
            staging_responses_fd,
            case_responses_fd,
            staging_fd,
            case_fd,
        ):
            if descriptor >= 0:
                os.close(descriptor)


raise SystemExit(main())
PY
```

If the UI offers only a copy operation, save that one copied response into the
closed staging file without reformatting. The staging file then represents the
exact byte sequence supplied by the operator; it is not authenticated provider
transport evidence.

Never trim whitespace, repair JSON, add or remove Markdown fences, merge
messages, normalize line endings, reformat through an editor, or overwrite a
response leaf. Both writers must be closed before this block runs. The
component-by-component no-follow opens, stable source witnesses, byte-for-byte
descriptor comparison, exclusive 0600 destinations, file fsync, and parent
fsync must all succeed before readiness is signalled.

## 12. Exact readiness control

Only after both response files are complete, closed, regular, non-symlinked,
and stable, return to the foreground capture terminal and enter exactly:

```text
H2C_RESPONSES_READY
```

Terminate it with exactly one LF. Extra spaces, quotes, casing changes,
additional text, a missing LF, or CRLF fail closed. There is no retry of the
control line within the case.

## 13. Success and exit contracts

The committed exit meanings are:

```text
0 = session returned successfully
1 = unexpected code failure
2 = argparse usage failure
3 = controlled session failure
```

On exit 0, stdout consists of exactly two LF-terminated lines:

```text
comparison_report_identity_sha256=<lowercase hex64>
receipt_identity_sha256=<lowercase hex64>
```

There is no bundle-identity stdout line. On a successful real foreground
capture, stderr consists of exactly this one LF-terminated readiness line:

```text
H2C prompts are ready; populate both response files, then enter H2C_RESPONSES_READY exactly.
```

Successful stderr is not empty. Do not filter the readiness line from the
retained stderr bytes. After CLI and launcher success, the mandatory retained-
log gate above reads both fixed paths through verified descriptors and requires
a byte-level full match: exactly two ordered stdout lines with lowercase
64-character hexadecimal identities and one LF each, and exactly the one
readiness stderr line with one LF. CR bytes, leading or trailing bytes, a third
identity or other extra line, swapped or altered labels, uppercase hexadecimal,
prefixes, timestamps, annotations, controlled-failure text, and tracebacks all
fail the gate.

Production CLI exit 0 also requires all seven session leaves and a confirmed
receipt. Receipt is the final session completion marker. Only CLI exit 0,
successful launcher/log capture, and verifier exit 0 permit the operator label
`CAPTURE_COMPLETE_UNVALIDATED`; it remains unvalidated until disk-only portable
validation passes.

Controlled exit 3 writes:

```text
H2C_CAPTURE_FAILED <CODE>
```

If the controlled failure occurs after prompt exposure, `cli_stderr.txt` may
contain the readiness line followed by the controlled-failure line. Argparse
exit 2 occurs before session invocation. An unexpected exception is an exit-1
code failure, not an operator-input failure.

The committed controlled vocabulary is:

| Code | Failure class |
|---|---|
| `H2C_ARGUMENT_INVALID` | `OPERATOR_INPUT` |
| `H2C_PATH_CONTRACT_INVALID` | `OPERATOR_INPUT` |
| `H2C_CAPABILITY_UNAVAILABLE` | `AVAILABILITY_PERMISSION` |
| `H2C_SOURCE_CAPTURE_INVALID` | `ARTIFACT_CONTENT` |
| `H2C_PORTFOLIO_NOT_COMPARABLE` | `ARTIFACT_CONTENT` |
| `H2C_LIVE_CHAIN_INVALID` | `VALIDATOR_SCHEMA` |
| `H2C_PROMPT_CONTRACT_INVALID` | `PROMPT_CONTRACT` |
| `H2C_LEGACY_COMPILER_INVALID` | `COMPILER_NORMALIZER` |
| `H2C_PROMPT_EXPOSURE_FAILED` | `PERSISTENCE` |
| `H2C_OPERATOR_CANCELLED` | `OPERATOR_INPUT` |
| `H2C_OPERATOR_CONTROL_INVALID` | `OPERATOR_INPUT` |
| `H2C_RESPONSE_INPUT_INVALID` | `OPERATOR_INPUT` |
| `H2C_RESPONSE_CONTENT_INVALID` | `ARTIFACT_CONTENT` |
| `H2C_CASE_EVIDENCE_BUNDLE_VALIDATION_INVALID` | `VALIDATOR_SCHEMA` |
| `H2C_H2_VALIDATION_INVALID` | `VALIDATOR_SCHEMA` |
| `H2C_RECEIPT_VALIDATION_INVALID` | `VALIDATOR_SCHEMA` |
| `H2C_CASE_EVIDENCE_BUNDLE_PERSISTENCE_FAILED` | `PERSISTENCE` |
| `H2C_H2_PERSISTENCE_FAILED` | `PERSISTENCE` |
| `H2C_RECEIPT_PERSISTENCE_FAILED` | `PERSISTENCE` |

There are 19 codes and eight closed failure classes. The eighth class,
`WORKFLOW_ORCHESTRATOR`, currently owns no public session code.

## 14. Partial persistence and retry

Result persistence is strictly ordered:

```text
case evidence bundle
→ H2 comparison report
→ receipt
```

The partial outcomes are:

```text
bundle failure:
no confirmed bundle
no H2 attempt
no receipt attempt

H2 failure:
confirmed bundle may remain
no confirmed H2
no receipt attempt

receipt failure:
confirmed bundle and H2 may remain
no confirmed receipt
```

The failing leaf may physically exist but remain unconfirmed. Prompt and
response files may already exist because those stages precede result
persistence. No confirmed receipt means `CAPTURE_INCOMPLETE`.

Do not edit partial evidence, create a receipt manually, resume the same case,
reuse the seven paths, clean up automatically, or retry automatically. Retain
the failed case unchanged under its consumed ID. A later attempt uses a new ID,
new directories, and seven new absent leaves.

## 15. Disk-only public portable validation

There is no checked-in portable-validation CLI. Only after production CLI exit
0, launcher success, verifier exit 0, and recording
`case_state=CAPTURE_COMPLETE_UNVALIDATED` in the private operator-notes staging
file under the Section 16 discipline, run the following read-only procedure
from the pinned repository. It imports only public production APIs, reads all
evidence from disk, obtains all six mappings from the persisted bundle, writes
nothing, and imports no test or private session helper.

```bash
set -euo pipefail
umask 077

H2C_REPOSITORY_ROOT=/absolute/path/to/investment-system-yu
H2C_CASE_ROOT=/absolute/path/to/the/existing/case
[[ "$H2C_REPOSITORY_ROOT" = /* ]]
cd -- "$H2C_REPOSITORY_ROOT"
[[ "$(pwd -P)" = "$H2C_REPOSITORY_ROOT" ]]
[[ "$(git rev-parse HEAD)" = 9488d389d00a4e21a5842c4fa59aa95ff7af1d09 ]]

PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src \
.venv/bin/python - "$H2C_CASE_ROOT" <<'PY'
from __future__ import annotations

import errno
import json
import os
import stat
import sys

from investment_orchestrator.offline.mmi_h2c_case_bundle_v1 import (
    MmiH2cCaseEvidenceBundleV1Error,
    validate_mmi_h2c_case_evidence_bundle_v1,
)
from investment_orchestrator.offline.mmi_h2c_dual_side_manual_handoff_context_receipt_v1 import (
    MmiH2cDualSideManualHandoffContextReceiptV1Error,
    validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence,
)


class _KnownInputFailure(ValueError):
    pass


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _components(value: str, *, absolute: bool) -> list[str]:
    if absolute != os.path.isabs(value):
        raise _KnownInputFailure(value)
    components = [part for part in value.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise _KnownInputFailure(value)
    return components


def _open_case_root(value: str) -> int:
    descriptor = -1
    try:
        descriptor = os.open("/", _DIRECTORY_FLAGS)
        for component in _components(value, absolute=True):
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise _KnownInputFailure("case_root") from exc
    except _KnownInputFailure:
        if descriptor >= 0:
            os.close(descriptor)
        raise


def _bytes(root_fd: int, relative: str) -> bytes:
    components = _components(relative, absolute=False)
    parent_fd = os.dup(root_fd)
    file_fd = -1
    try:
        for component in components[:-1]:
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=parent_fd,
            )
            os.close(parent_fd)
            parent_fd = child
        file_fd = os.open(
            components[-1],
            _FILE_FLAGS,
            dir_fd=parent_fd,
        )
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise _KnownInputFailure(relative)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(file_fd, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(file_fd)
        witness_before = (
            before.st_dev,
            before.st_ino,
            before.st_mode,
            before.st_size,
            before.st_mtime_ns,
            before.st_ctime_ns,
        )
        witness_after = (
            after.st_dev,
            after.st_ino,
            after.st_mode,
            after.st_size,
            after.st_mtime_ns,
            after.st_ctime_ns,
        )
        if witness_before != witness_after:
            raise _KnownInputFailure(relative)
        return b"".join(chunks)
    except OSError as exc:
        raise _KnownInputFailure(relative) from exc
    finally:
        if file_fd >= 0:
            os.close(file_fd)
        os.close(parent_fd)


def _mapping(root_fd: int, relative: str) -> dict[str, object]:
    try:
        value = json.loads(_bytes(root_fd, relative))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise _KnownInputFailure(relative) from exc
    if type(value) is not dict:
        raise _KnownInputFailure(relative)
    return value


def main() -> int:
    if len(sys.argv) != 2:
        print("CAPTURE_VALIDATION_FAILED", file=sys.stderr)
        return 3
    try:
        root_fd = _open_case_root(sys.argv[1])
        try:
            bundle = _mapping(
                root_fd, "artifacts/case_evidence_bundle.json"
            )
            comparison_report = _mapping(
                root_fd, "artifacts/comparison_report.json"
            )
            receipt = _mapping(root_fd, "artifacts/receipt.json")
            validate_mmi_h2c_case_evidence_bundle_v1(bundle=bundle)
            validate_mmi_h2c_dual_side_manual_handoff_context_receipt_v1_portable_evidence(
                receipt=receipt,
                comparison_report=comparison_report,
                legacy_step1_compatibility_candidate=bundle[
                    "legacy_step1_compatibility_candidate"
                ],
                validated_grounded_analysis_response=bundle[
                    "validated_grounded_analysis_response"
                ],
                raw_response_envelope=bundle["raw_response_envelope"],
                grounded_prompt=bundle["grounded_prompt"],
                archived_h1_prompt_bytes=_bytes(
                    root_fd, "prompts/h1_prompt.txt"
                ),
                archived_h1_response_bytes=_bytes(
                    root_fd, "responses/h1_response.raw"
                ),
                archived_legacy_response_bytes=_bytes(
                    root_fd, "responses/legacy_response.raw"
                ),
                archived_strategy_settings_bytes=_bytes(
                    root_fd, "archive/strategy_settings.yaml"
                ),
                strategy_settings_source_record=bundle[
                    "strategy_settings_source_record"
                ],
                archived_portfolio_snapshot_bytes=_bytes(
                    root_fd, "archive/portfolio_snapshot.txt"
                ),
                portfolio_snapshot_source_record=bundle[
                    "portfolio_snapshot_source_record"
                ],
                archived_legacy_prompt_template_bytes=_bytes(
                    root_fd, "archive/research_dual_lane.txt"
                ),
                archived_legacy_prompt_bytes=_bytes(
                    root_fd, "prompts/legacy_prompt.txt"
                ),
            )
        finally:
            os.close(root_fd)
    except _KnownInputFailure:
        print("CAPTURE_VALIDATION_FAILED", file=sys.stderr)
        return 3
    except MmiH2cCaseEvidenceBundleV1Error as exc:
        if exc.code != "MMI_H2C_CASE_EVIDENCE_BUNDLE_V1_INVALID":
            raise
        print("CAPTURE_VALIDATION_FAILED", file=sys.stderr)
        return 3
    except MmiH2cDualSideManualHandoffContextReceiptV1Error as exc:
        if exc.code not in {
            "MMI_H2C_RECEIPT_V1_INVALID",
            "MMI_H2C_PORTABLE_EVIDENCE_INVALID",
        }:
            raise
        print("CAPTURE_VALIDATION_FAILED", file=sys.stderr)
        return 3
    print("CAPTURE_PORTABLE_VALIDATED")
    return 0


raise SystemExit(main())
PY
```

The public validator checks the bundle mappings, H2, receipt, prompt bytes,
response bytes, archived settings, archived portfolio, archived legacy
template, deterministic legacy-prompt reconstruction, identities, and links.
In particular, the archive template hash must match the template hash bound in
the receipt. Unexpected programming errors are not caught and remain exit 1.

## 16. Operator labels and human comparison

Use only these labels in the private operator-notes staging file before its
one-time finalization as `operator_notes.md`:

```text
CAPTURE_INCOMPLETE
CAPTURE_COMPLETE_UNVALIDATED
CAPTURE_PORTABLE_VALIDATED
CAPTURE_VALIDATION_FAILED
```

They are operator-note labels, not schema values or code inputs. Update only
the private 0600 staging leaf beneath the private 0700 staging root, keep each
writer closed outside the actual update, and never append to or reopen the
retained case pathname. The prompt and response blocks append their witnesses
through a verified staging-`meta` descriptor; the finalization block later
opens the completed staging notes through the same component-by-component
no-follow discipline. Record `case_state=CAPTURE_COMPLETE_UNVALIDATED` only
after production CLI exit 0, successful launcher/log capture, and exact
retained-log verification. After the public procedure, record exactly one final
label:
`final_case_label=CAPTURE_PORTABLE_VALIDATED` for validation exit 0 or
`final_case_label=CAPTURE_VALIDATION_FAILED` for validation exit 3. Do not
erase the state transition. A case without a confirmed receipt records
`final_case_label=CAPTURE_INCOMPLETE` and does not invoke portable validation.
A case whose retained-log verification fails also remains
`CAPTURE_INCOMPLETE`, even if the production session returned successfully and
a confirmed receipt exists; do not invoke portable validation for that case.

Only a portable-validated case is eligible for human prospective comparison.
That comparison may discuss:

```text
structural validity
instrument/reference agreement
unsupported assertions
missing-source patterns
material conclusion differences
legacy-only information
H1-only information
operator usefulness
whether either lane recommends HOLD or NO_TRADE
```

The comparison remains operator-authored, non-authoritative, and unconsumed by
deterministic code. It creates no score, threshold, readiness gate, permission,
publication, order, or broker authority.

## 17. Final notes, inventory, and permissions

For a production exit-0 session, the finalization order is mandatory:

```text
session and foreground launcher exit successfully
→ retained CLI logs read back through verified descriptors
→ exact stdout and stderr bytes verified
→ CAPTURE_COMPLETE_UNVALIDATED recorded
→ portable validation attempted
→ final operator case label recorded
→ operator notes finalized and closed
→ sha256_inventory.txt created last
→ retained-case permissions finalized
```

After writing the final label and any human comparison, close the private
operator-notes staging file. The block below copies it once into the absent
retained `operator_notes.md` leaf. It then generates and verifies the complete
inventory in private staging before the final inventory leaf is created. The
inventory lists every other retained case file and explicitly excludes only
itself, because a file cannot contain its own final digest.

```bash
set -euo pipefail
umask 077

H2C_REPOSITORY_ROOT=/absolute/path/to/investment-system-yu
H2C_CASE_ROOT=/absolute/path/to/the/existing/case
H2C_STAGING_ROOT=/absolute/path/to/the/private/staging/root

[[ "$H2C_REPOSITORY_ROOT" = /* ]]
cd -- "$H2C_REPOSITORY_ROOT"
[[ "$(pwd -P)" = "$H2C_REPOSITORY_ROOT" ]]
[[ "$(git rev-parse HEAD)" = 9488d389d00a4e21a5842c4fa59aa95ff7af1d09 ]]
[[ "$H2C_CASE_ROOT" = /* ]]
[[ "$H2C_STAGING_ROOT" = /* ]]
[[ "$H2C_STAGING_ROOT" != /tmp && "$H2C_STAGING_ROOT" != /tmp/* ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT" ]]
[[ "$H2C_STAGING_ROOT" != "$H2C_CASE_ROOT/"* ]]

PYTHONDONTWRITEBYTECODE=1 \
.venv/bin/python /dev/fd/3 \
  "$H2C_CASE_ROOT" "$H2C_STAGING_ROOT" 3<<'PY'
from __future__ import annotations

import errno
import hashlib
import os
import stat
import sys


_DIRECTORY_FLAGS = (
    os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
)
_FILE_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
_NEW_FILE_FLAGS = (
    os.O_RDWR
    | os.O_CREAT
    | os.O_EXCL
    | os.O_NOFOLLOW
    | os.O_CLOEXEC
)
_EXPECTED = {
    "archive": {
        "portfolio_snapshot.txt": "archived_portfolio_snapshot",
        "research_dual_lane.txt": "archived_legacy_template",
        "strategy_settings.yaml": "archived_strategy_settings",
    },
    "artifacts": {
        "case_evidence_bundle.json": "case_evidence_bundle",
        "comparison_report.json": "comparison_report",
        "receipt.json": "receipt",
    },
    "meta": {
        "cli_stderr.txt": "cli_stderr",
        "cli_stdout.txt": "cli_stdout",
        "expected_sha256.txt": "expected_hash_metadata",
        "operator_notes.md": "operator_notes",
        "repo_commit.txt": "repository_commit_metadata",
    },
    "prompts": {
        "h1_prompt.txt": "h1_prompt",
        "legacy_prompt.txt": "legacy_prompt",
    },
    "responses": {
        "h1_response.raw": "h1_response",
        "legacy_response.raw": "legacy_response",
    },
}


def _open_absolute_directory(path: str) -> int:
    if not os.path.isabs(path):
        raise OSError(errno.EINVAL, "case root must be absolute")
    components = [part for part in path.split("/") if part]
    if not components or any(part in {".", ".."} for part in components):
        raise OSError(errno.EINVAL, "case root is not lexical")
    descriptor = os.open("/", _DIRECTORY_FLAGS)
    try:
        for component in components:
            child = os.open(
                component,
                _DIRECTORY_FLAGS,
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except OSError:
        os.close(descriptor)
        raise


def _witness(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_mode,
        value.st_uid,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _read_stable(directory_fd: int, name: str) -> bytes:
    descriptor = os.open(name, _FILE_FLAGS, dir_fd=directory_fd)
    try:
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise OSError(errno.EINVAL, "retained leaf is not regular")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        after = os.fstat(descriptor)
        value = b"".join(chunks)
        if _witness(before) != _witness(after) or len(value) != before.st_size:
            raise OSError(errno.EBUSY, "retained leaf changed")
        return value
    finally:
        os.close(descriptor)


def _write_all(descriptor: int, value: bytes) -> None:
    view = memoryview(value)
    while view:
        written = os.write(descriptor, view)
        if written <= 0:
            raise OSError(errno.EIO, "short finalization write")
        view = view[written:]


def _write_new(directory_fd: int, name: str, value: bytes) -> bytes:
    descriptor = os.open(
        name,
        _NEW_FILE_FLAGS,
        0o600,
        dir_fd=directory_fd,
    )
    try:
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or stat.S_IMODE(created.st_mode) != 0o600
        ):
            raise OSError(errno.EPERM, "new finalization leaf is not private")
        _write_all(descriptor, value)
        os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 65_536)
            if not chunk:
                break
            chunks.append(chunk)
        persisted = b"".join(chunks)
        if persisted != value:
            raise OSError(errno.EIO, "finalization comparison failed")
        return persisted
    finally:
        os.close(descriptor)


def _file_record(directory_fd: int, name: str) -> tuple[int, str]:
    value = _read_stable(directory_fd, name)
    return len(value), hashlib.sha256(value).hexdigest()


def main() -> int:
    if len(sys.argv) != 3:
        raise OSError(errno.EINVAL, "case and staging roots required")
    root_fd = _open_absolute_directory(sys.argv[1])
    staging_fd = _open_absolute_directory(sys.argv[2])
    staging_meta_fd = -1
    case_meta_fd = -1
    records: list[tuple[str, str, int, str]] = []
    try:
        staging_stat = os.fstat(staging_fd)
        if (
            staging_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging root is not private")
        staging_meta_fd = os.open(
            "meta", _DIRECTORY_FLAGS, dir_fd=staging_fd
        )
        staging_meta_stat = os.fstat(staging_meta_fd)
        if (
            staging_meta_stat.st_uid != os.geteuid()
            or stat.S_IMODE(staging_meta_stat.st_mode) != 0o700
        ):
            raise OSError(errno.EPERM, "staging metadata is not private")
        case_meta_fd = os.open("meta", _DIRECTORY_FLAGS, dir_fd=root_fd)
        notes = _read_stable(
            staging_meta_fd, "operator_notes.md.staging"
        )
        _write_new(case_meta_fd, "operator_notes.md", notes)
        os.fsync(case_meta_fd)

        if set(os.listdir(root_fd)) != set(_EXPECTED):
            raise OSError(errno.EINVAL, "unexpected case directory set")
        for directory_name, expected_files in _EXPECTED.items():
            directory_fd = os.open(
                directory_name,
                _DIRECTORY_FLAGS,
                dir_fd=root_fd,
            )
            try:
                if set(os.listdir(directory_fd)) != set(expected_files):
                    raise OSError(
                        errno.EINVAL,
                        "unexpected retained file set",
                    )
                for file_name, role in expected_files.items():
                    byte_count, sha256 = _file_record(
                        directory_fd, file_name
                    )
                    records.append(
                        (
                            f"{directory_name}/{file_name}",
                            role,
                            byte_count,
                            sha256,
                        )
                    )
            finally:
                os.close(directory_fd)
        records.sort(key=lambda record: record[0])
        lines = [
            "# operator-authored, non-authoritative retained-case inventory",
            "# excludes only meta/sha256_inventory.txt "
            "(self-digest is impossible)",
            "# relative_path<TAB>role<TAB>type<TAB>bytes<TAB>sha256",
        ]
        for relative, role, byte_count, sha256 in records:
            lines.append(
                f"{relative}\t{role}\tregular\t{byte_count}\t{sha256}"
            )
        if len(lines) != 18 or [record[0] for record in records] != sorted(
            record[0] for record in records
        ):
            raise OSError(errno.EINVAL, "inventory coverage or order failed")
        inventory = ("\n".join(lines) + "\n").encode()
        staged_inventory = _write_new(
            staging_meta_fd,
            "sha256_inventory.txt.staging",
            inventory,
        )
        if staged_inventory.count(b"\n") != 18:
            raise OSError(errno.EINVAL, "staging inventory line count failed")
        _write_new(case_meta_fd, "sha256_inventory.txt", staged_inventory)
        os.fsync(staging_meta_fd)
        os.fsync(case_meta_fd)

        final_expected = {
            directory: set(files) for directory, files in _EXPECTED.items()
        }
        final_expected["meta"].add("sha256_inventory.txt")
        os.fchmod(root_fd, 0o700)
        for directory_name, expected_files in final_expected.items():
            directory_fd = os.open(
                directory_name,
                _DIRECTORY_FLAGS,
                dir_fd=root_fd,
            )
            try:
                if set(os.listdir(directory_fd)) != expected_files:
                    raise OSError(errno.EINVAL, "final retained set changed")
                os.fchmod(directory_fd, 0o700)
                for file_name in expected_files:
                    file_fd = os.open(
                        file_name, _FILE_FLAGS, dir_fd=directory_fd
                    )
                    try:
                        if not stat.S_ISREG(os.fstat(file_fd).st_mode):
                            raise OSError(
                                errno.EINVAL, "final leaf is not regular"
                            )
                        os.fchmod(file_fd, 0o600)
                    finally:
                        os.close(file_fd)
            finally:
                os.close(directory_fd)
        return 0
    finally:
        for descriptor in (case_meta_fd, staging_meta_fd, staging_fd, root_fd):
            if descriptor >= 0:
                os.close(descriptor)


raise SystemExit(main())
PY
```

After inventory creation, do not change the contents of operator notes or any
other retained case file. Permission tightening follows only because mode bits
are intentionally not inventory fields; it changes no inventoried content,
type, byte count, or SHA-256. The inventory contains no passwords, credentials,
cookies, or provider-session data. Do not create a latest, active, publication,
or case-registry pointer. If enumeration, hashing, sorting, staging output, or
the 18-line coverage check fails, the final inventory leaf remains absent; a
partial staging inventory is never finalized. If the one exclusive final-copy
write or fsync itself fails, its physical leaf may exist but is unconfirmed.

## 18. Failure and retry matrix

After any case creation or session side effect, the default is: retain the
attempt unchanged, consume its case ID, and use a new ID for any later attempt.
In this matrix, a physical receipt leaf is merely a directory entry. A
confirmed receipt exists only after the session's durable receipt write
returns successfully. “Complete” never means portable-validated; portable
validation is stated separately and must itself pass.

| State | Possible side effects | Receipt | Complete? | Reuse ID? | Required evidence handling |
|---|---|---|---|---|---|
| Case-root collision | The existing root may contain prior evidence; this attempt creates nothing attributable to itself | Existing receipt, if any, is not attributable to this attempt | No | No | Leave the existing root untouched; choose a new ID |
| Repository mismatch | None; preflight stops before case creation | No | No | No | Record the mismatch outside a case and stop |
| Archive copy/hash mismatch | Case directories, partial archives, and metadata may exist | No | No | No | Retain the whole attempt unchanged |
| Argparse exit 2 | Archives, metadata, and stream logs exist; session is not invoked | No | No | No | Retain the command context and stderr |
| Path-contract exit 3 | Operator archives and metadata exist; session preflight creates no session leaf | No | No | No | Retain paths, hashes, stdout, and stderr |
| Partial prompt exposure | One or both prompt leaves may exist; first-prompt cleanup is best effort when the second write fails | No | No | No | Retain every surviving leaf; do not repair |
| Operator cancellation | Prompts and any operator-created response evidence may exist | No | No | No | Label incomplete and retain unchanged |
| Invalid response input/content | Prompts and both response leaves may exist; no confirmed result artifact | No | No | No | Retain exact raw responses and failure code |
| Bundle persistence failure | Prompts/responses exist; the failing bundle leaf may exist but is unconfirmed; H2 and receipt are unattempted | No | No | No | Retain all leaves and the exact failure code |
| H2 persistence failure | Confirmed bundle may remain; failing H2 leaf may exist but is unconfirmed; receipt is unattempted | No | No | No | Retain bundle, failing leaf, and logs |
| Receipt persistence failure | Confirmed bundle and H2 may remain; failing receipt leaf may exist but is unconfirmed | Possibly physically present, never confirmed | No | No | Retain all evidence; never create or edit a receipt |
| Unexpected exit 1 | Side effects depend on the failure point; even a receipt may physically exist | May exist; status is indeterminate | Treat as incomplete | No | Preserve everything and classify as a code failure |
| Exit 0 followed by portable-validation failure | All seven session leaves and a confirmed receipt exist | Yes | Not portable-valid | No | Label `CAPTURE_VALIDATION_FAILED`; retain immutably |

The stream, validation, and finalization subcases are fail-closed:

* CLI exit 1, 2, or 3 with successful log capture retains the exact logs and
  follows the corresponding row above; it never reaches success processing.
* CLI exit 0 with failed log creation, copying, or fsync may have a physical
  and session-confirmed receipt, but the retained stream evidence is
  unconfirmed. Treat the case as incomplete, retain it, and consume the ID.
* CLI exit 0 and launcher success followed by a retained stdout mismatch is a
  documentation-verifier exit 3, not a production CLI exit 3. All seven session
  artifacts and a confirmed receipt may exist, but the operator case is
  `CAPTURE_INCOMPLETE`; do not run portable validation or inventory, retain all
  evidence unchanged, consume the ID, and use a new ID for another attempt.
* CLI exit 0 and launcher success followed by a retained stderr mismatch has
  the same incomplete posture: a confirmed receipt may exist, portable
  validation and inventory are unattempted, all evidence is retained unchanged,
  and any new attempt requires a new ID.
* A missing, non-regular, changing, unreadable, or symlinked retained log or an
  unsafe case-root or `meta` component is a documentation-verifier exit 3. The
  production session may have returned successfully and a confirmed receipt
  may exist, but the operator case remains incomplete. Do not delete or repair
  either log; do not validate or inventory; retain the case, consume its ID,
  and use a new ID for another attempt.
* Portable-validation wrong arity, unsafe root, intermediate symlink, missing
  file, or invalid content exits 3. A physical and session-confirmed receipt
  may exist, but portable validation did not pass; record
  `CAPTURE_VALIDATION_FAILED`, retain the case, and consume the ID.
* Inventory enumeration, hashing, sorting, staging, coverage, or final-copy
  failure occurs only after the session and validation attempt. A confirmed
  receipt and even a successful portable validation may exist, but retained
  case finalization is incomplete. The final inventory must be absent or
  treated as an unconfirmed failing leaf; retain everything and consume the
  ID.

## 19. No-automation and no-authority summary

This procedure is manual end to end. A portable-validated case is evidence for
human prospective comparison only. It does not activate weekly routing, replace
legacy Step 1, alter Steps 2–4, establish availability or permission, pass a
gate, change final safety, publish anything, create a pointer, compile or place
an order, or communicate with a broker.
