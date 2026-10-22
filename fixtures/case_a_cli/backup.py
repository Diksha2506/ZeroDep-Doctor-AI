"""Small CLI tool — backup utility with argparse, pathlib, logging, and third-party deps."""

import argparse
import logging
import pathlib
import hashlib
import shutil
import time

import requests  # third-party: HTTP for upload
import click     # third-party: alternate CLI framework (duplicate of argparse)

# Logging setup (could use loguru instead — currently a third-party dep)
try:
    from loguru import logger
    USE_LOGURU = True
except ImportError:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logger = logging.getLogger(__name__)
    USE_LOGURU = False


def hash_file(path: pathlib.Path) -> str:
    """Compute SHA-256 of a file for integrity checking."""
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def backup_directory(src: pathlib.Path, dest: pathlib.Path) -> int:
    """Copy files from src to dest, returning count of copied files."""
    copied = 0
    for item in src.rglob("*"):
        if item.is_file():
            target = dest / item.relative_to(src)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, target)
            copied += 1
    return copied


def upload_report(url: str, report: dict) -> bool:
    """POST backup report to a remote endpoint."""
    try:
        resp = requests.post(url, json=report, timeout=10)
        return resp.status_code == 200
    except requests.RequestException as exc:
        logger.error(f"Upload failed: {exc}")
        return False


@click.command()
@click.option("--src", required=True, help="Source directory to back up")
@click.option("--dest", required=True, help="Backup destination directory")
@click.option("--report-url", default=None, help="Optional URL to POST backup report")
def backup_cmd(src, dest, report_url):
    """Backup CLI entry point via click."""
    src_path = pathlib.Path(src)
    dest_path = pathlib.Path(dest)

    if not src_path.is_dir():
        logger.error(f"Source directory not found: {src}")
        raise SystemExit(1)

    start = time.time()
    count = backup_directory(src_path, dest_path)
    elapsed = time.time() - start

    logger.info(f"Backed up {count} files in {elapsed:.2f}s")

    if report_url:
        report = {"files": count, "duration": elapsed, "source": str(src_path)}
        success = upload_report(report_url, report)
        logger.info(f"Report upload: {'OK' if success else 'FAILED'}")


def main():
    """Argparse entry point (alternative to click interface)."""
    parser = argparse.ArgumentParser(description="Simple backup utility")
    parser.add_argument("src", help="Source directory")
    parser.add_argument("dest", help="Destination directory")
    args = parser.parse_args()

    src_path = pathlib.Path(args.src)
    dest_path = pathlib.Path(args.dest)
    count = backup_directory(src_path, dest_path)
    print(f"Backed up {count} files.")


if __name__ == "__main__":
    main()
