"""Claim stable files from the active library's inbox before importing them."""

import os
import re
import stat
from uuid import uuid4

from services.import_pipeline import IMPORT_FORMATS


STAGED = re.compile(r"[a-f0-9]{32}_.+")
MAX_BATCH = 64


def _plain(path, match):
    try:
        info = path.lstat()
    except OSError:
        return False
    return match(info.st_mode) and not _reparse_point(info)


def _reparse_point(info):
    return getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT


def _signature(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or _reparse_point(info):
        return None
    return info.st_size, info.st_mtime_ns // 100


def claim_ready(root, candidates):
    inbox = root / "inbox"
    holding = inbox / ".processing"
    failed = inbox / "failed"
    if not _plain(inbox, stat.S_ISDIR):
        return []
    holding.mkdir(exist_ok=True)
    failed.mkdir(exist_ok=True)
    if not all(_plain(path, stat.S_ISDIR) for path in (holding, failed)):
        return []
    claimed = []
    for candidate in candidates[:MAX_BATCH]:
        name = candidate.get("name", "")
        if (not isinstance(name, str) or not name or name in {".", ".."} or any(char in name for char in "/\\:\x00")
                or not name.lower().endswith(IMPORT_FORMATS)):
            continue
        staged = candidate.get("staged") is True
        if staged and not STAGED.fullmatch(name):
            continue
        source = (holding if staged else inbox) / name
        expected = candidate.get("length"), candidate.get("modified")
        try:
            if not expected[0] or _signature(source) != expected:
                continue
            if not staged:
                # Stage on the same volume. A new download can safely reuse its original
                # name while only this claimed version can be consumed by the import job.
                target = holding / f"{uuid4().hex}_{name[-180:]}"
                source.rename(target)
                source = target
            if _signature(source) == expected:
                claimed.append(os.fspath(source))
            # A file changed during the claim: keep it in staging for the next poll.
        except OSError:
            continue
    return claimed
