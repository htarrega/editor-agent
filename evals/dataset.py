"""Loads the clean fragments that both corpora are built from.

Corpus A is these fragments run through the corruptor; corpus B is the very
same fragments left untouched. Drop any ``.txt`` into ``evals/corpus/`` and it
joins both.
"""

import pathlib
import re

from pydantic import BaseModel

CORPUS_DIR = pathlib.Path(__file__).parent / "corpus"


class Fragment(BaseModel):
    name: str
    text: str

    @property
    def words(self):
        return len(self.text.split())


def load_fragments(directory=None, limit_words=None, only=None):
    directory = pathlib.Path(directory) if directory else CORPUS_DIR
    paths = sorted(directory.glob("*.txt"))
    if not paths:
        raise FileNotFoundError(f"no .txt fragments in {directory}")

    fragments = []
    for path in paths:
        name = path.stem
        if only and name not in only:
            continue
        text = normalise(path.read_text(encoding="utf-8"))
        if limit_words:
            text = truncate(text, limit_words)
        fragments.append(Fragment(name=name, text=text))
    if not fragments:
        raise FileNotFoundError(f"no fragments matched {only} in {directory}")
    return fragments


def normalise(text):
    """Remove noise that has nothing to do with language.

    A byte-order mark, trailing spaces and runs of blank lines would be
    flagged by every system and would drown the false-positive signal we
    actually care about.
    """
    text = text.lstrip("﻿")
    text = text.replace("\r\n", "\n").replace(" ", " ")
    text = "\n".join(line.rstrip() for line in text.split("\n"))
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def truncate(text, limit_words):
    """Cut at a paragraph boundary so the text stays readable."""
    kept, total = [], 0
    for paragraph in text.split("\n\n"):
        kept.append(paragraph)
        total += len(paragraph.split())
        if total >= limit_words:
            break
    return "\n\n".join(kept).strip() + "\n"
