"""Redact server and workstation paths from public RAG diagnostics."""

import re


LOCAL_PATH = re.compile(r"(?<![A-Za-z0-9])/(?:Users|home|var/www|etc)/[^\s<>\"'`]+")


def redact_local_paths(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        matched = match.group()
        path = matched.rstrip(".,;:!?")
        return "[redacted local path]" + matched[len(path):]

    return LOCAL_PATH.sub(replace, value)
