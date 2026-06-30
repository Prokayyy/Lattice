#!/usr/bin/env python3
"""Periodic local security hygiene agent.

The agent is intentionally conservative. It does not read or print `.env`
contents. With `--fix`, it only applies reversible local hygiene fixes:

1. chmod secret-bearing local files to 0600.
2. repair whitespace issues reported by `git diff --check`.

Other checks are printed and written to data/security_agent_report.json for
operator review.
"""

import argparse
import json
import os
import re
import stat
import subprocess
import sys
import time
from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
REPORT_PATH = REPO / "data" / "security_agent_report.json"

SECRET_FILE_GLOBS = (
    ".env",
    ".env.*",
    "*.session",
    "*.session-journal",
    "*.session-shm",
    "*.session-wal",
    "*.session-*",
    "data/*.session",
    "data/*.session*",
    "data/*login*.json",
)

SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b"
    r"([A-Z0-9_]*(?:API[_-]?KEY|PRIVATE[_-]?KEY|SECRET|PASSWORD|"
    r"BEARER[_-]?TOKEN|AUTH[_-]?TOKEN)[A-Z0-9_]*)"
    r"\b\s*[:=]\s*[\"']?([A-Za-z0-9_./+=:@$-]{24,})"
)

PLACEHOLDER_MARKERS = (
    "example",
    "placeholder",
    "changeme",
    "replace",
    "your_",
    "xxx",
    "<",
    "${",
    "self.",
    "os.",
    "config.",
    "getenv",
    "get_env",
    "_env_",
)

SECRET_SCAN_SKIP_NAMES = {
    ".env.example",
}

SECRET_SCAN_SKIP_SUFFIXES = (
    ".md",
    ".csv",
    ".json",
    ".jsonl",
    ".gz",
    ".db",
    ".sqlite",
    ".sqlite3",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".session",
)

UNTRACKED_SECRET_NAME_RE = re.compile(
    r"(?i)(^|/|\.)(?:env|session|secret|private[-_]?key|api[-_]?key|"
    r"auth[-_]?token|bearer[-_]?token|credential|wallet|mnemonic)(?:$|[._/-])"
)

UNTRACKED_SECRET_SUFFIXES = (
    ".session",
    ".session-journal",
    ".session-shm",
    ".session-wal",
    ".pem",
    ".key",
)


def now_stamp():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def run(cmd, timeout=30):
    return subprocess.run(
        cmd,
        cwd=REPO,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def rel(path):
    try:
        return str(path.relative_to(REPO))
    except ValueError:
        return str(path)


class Report:
    def __init__(self, fix=False):
        self.fix = bool(fix)
        self.findings = []
        self.fixes = []
        self.errors = []

    def finding(self, check, severity, message, path=None, fixed=False):
        item = {
            "check": check,
            "severity": severity,
            "message": message,
            "fixed": bool(fixed),
        }
        if path:
            item["path"] = rel(Path(path))
        self.findings.append(item)

    def fixed(self, check, message, path=None):
        item = {
            "check": check,
            "message": message,
        }
        if path:
            item["path"] = rel(Path(path))
        self.fixes.append(item)

    def error(self, check, message):
        self.errors.append({
            "check": check,
            "message": message,
        })

    def payload(self):
        counts = {}
        for item in self.findings:
            counts[item["severity"]] = counts.get(item["severity"], 0) + 1
        if self.errors:
            status = "error"
        elif counts.get("high"):
            status = "high"
        elif counts.get("medium") or counts.get("low"):
            status = "warn"
        else:
            status = "pass"
        return {
            "at": time.time(),
            "status": status,
            "fix": self.fix,
            "summary": {
                "findings": len(self.findings),
                "fixes": len(self.fixes),
                "errors": len(self.errors),
                "by_severity": counts,
            },
            "findings": self.findings,
            "fixes": self.fixes,
            "errors": self.errors,
        }


def iter_secret_files():
    seen = set()
    for pattern in SECRET_FILE_GLOBS:
        for path in REPO.glob(pattern):
            if path.name == ".env.example" or not path.is_file():
                continue
            if path in seen:
                continue
            seen.add(path)
            yield path


def check_secret_file_permissions(report):
    for path in sorted(iter_secret_files()):
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError as exc:
            report.error("secret_file_permissions", f"{rel(path)}: {exc}")
            continue

        if mode & 0o077:
            if report.fix:
                try:
                    path.chmod(0o600)
                    report.fixed(
                        "secret_file_permissions",
                        f"chmod 0600 from {mode:04o}",
                        path,
                    )
                    report.finding(
                        "secret_file_permissions",
                        "medium",
                        f"secret-bearing file was {mode:04o}; chmodded to 0600",
                        path,
                        fixed=True,
                    )
                    continue
                except OSError as exc:
                    report.error(
                        "secret_file_permissions",
                        f"{rel(path)} chmod failed: {exc}",
                    )
            report.finding(
                "secret_file_permissions",
                "medium",
                f"secret-bearing file is {mode:04o}; expected 0600",
                path,
            )


def check_env_ignored(report):
    proc = run(["git", "check-ignore", "-q", ".env"])
    if proc.returncode != 0:
        report.finding(
            "env_gitignore",
            "high",
            ".env is not ignored by git",
            REPO / ".env",
        )


def git_ignored(path):
    proc = run(["git", "check-ignore", "-q", rel(path)])
    return proc.returncode == 0


def check_untracked_sensitive_files(report):
    proc = run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        timeout=60,
    )
    if proc.returncode != 0:
        report.error(
            "untracked_sensitive_files",
            proc.stderr.strip() or "git ls-files failed",
        )
        return

    for item in proc.stdout.split("\0"):
        if not item:
            continue
        path = REPO / item
        if not path.is_file():
            continue

        rel_path = rel(path)
        lower = rel_path.lower()
        suffix_hit = any(
            lower.endswith(suffix)
            for suffix in UNTRACKED_SECRET_SUFFIXES
        )
        name_hit = bool(UNTRACKED_SECRET_NAME_RE.search(rel_path))

        if not suffix_hit and not name_hit:
            continue

        report.finding(
            "untracked_sensitive_files",
            "high",
            "sensitive-looking untracked file is not ignored by git",
            path,
        )


def parse_diff_check_paths(output):
    paths = set()
    for line in output.splitlines():
        if not line:
            continue
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        path = REPO / parts[0]
        if path.exists() and path.is_file():
            paths.add(path)
    return paths


def repair_text_whitespace(path):
    try:
        raw = path.read_bytes()
    except OSError:
        return False

    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return False

    lines = [line.rstrip(" \t") for line in text.splitlines()]
    while lines and lines[-1] == "":
        lines.pop()

    new_text = "\n".join(lines)
    if new_text:
        new_text += "\n"

    if new_text == text:
        return False

    path.write_text(new_text, encoding="utf-8")
    return True


def check_git_diff_whitespace(report):
    proc = run(["git", "diff", "--check"])
    if proc.returncode == 0:
        return

    output = (proc.stdout or "") + (proc.stderr or "")
    if report.fix:
        fixed_paths = []
        for path in sorted(parse_diff_check_paths(output)):
            if repair_text_whitespace(path):
                fixed_paths.append(path)
                report.fixed(
                    "git_diff_check",
                    "repaired trailing whitespace / blank EOF",
                    path,
                )

        proc = run(["git", "diff", "--check"])
        if proc.returncode == 0:
            for path in fixed_paths:
                report.finding(
                    "git_diff_check",
                    "low",
                    "git diff whitespace issue repaired",
                    path,
                    fixed=True,
                )
            return
        output = (proc.stdout or "") + (proc.stderr or "")

    for line in output.splitlines():
        report.finding(
            "git_diff_check",
            "low",
            line,
        )


def tracked_files():
    proc = run(["git", "ls-files", "-z"], timeout=60)
    if proc.returncode != 0:
        return []
    return [REPO / item for item in proc.stdout.split("\0") if item]


def looks_like_placeholder(value):
    lowered = value.lower()
    return any(marker in lowered for marker in PLACEHOLDER_MARKERS)


def check_tracked_secret_literals(report):
    for path in tracked_files():
        rel_path = rel(path)
        if path.name in SECRET_SCAN_SKIP_NAMES:
            continue
        if rel_path.startswith(("data/", "logs/", ".git/")):
            continue
        if path.suffix.lower() in SECRET_SCAN_SKIP_SUFFIXES:
            continue
        try:
            if path.stat().st_size > 2_000_000:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for lineno, line in enumerate(text.splitlines(), 1):
            match = SECRET_ASSIGNMENT_RE.search(line)
            if not match:
                continue
            value = match.group(2).strip("\"'")
            if looks_like_placeholder(value):
                continue
            report.finding(
                "tracked_secret_literals",
                "medium",
                f"possible literal secret assignment at {rel_path}:{lineno}; value redacted",
                path,
            )


def write_outputs(payload, json_stdout=False):
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")

    summary = payload["summary"]
    line = (
        f"[{now_stamp()}] security_agent: status={payload['status']} "
        f"findings={summary['findings']} fixes={summary['fixes']} "
        f"errors={summary['errors']}"
    )

    if json_stdout:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(line)
        for item in payload["findings"][:10]:
            path = f" {item['path']}" if item.get("path") else ""
            fixed = " fixed" if item.get("fixed") else ""
            print(
                f"- {item['severity']} {item['check']}{path}{fixed}: "
                f"{item['message']}"
            )
        if len(payload["findings"]) > 10:
            print(f"- ... {len(payload['findings']) - 10} more findings")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fix",
        action="store_true",
        help="apply conservative local fixes",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full JSON report to stdout",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero when findings remain",
    )
    args = parser.parse_args()

    os.chdir(REPO)
    report = Report(fix=args.fix)
    check_secret_file_permissions(report)
    check_env_ignored(report)
    check_untracked_sensitive_files(report)
    check_git_diff_whitespace(report)
    check_tracked_secret_literals(report)

    payload = report.payload()
    write_outputs(payload, json_stdout=args.json)

    if payload["status"] == "error":
        return 2
    if args.strict and payload["summary"]["findings"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
