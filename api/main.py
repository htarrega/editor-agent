from functools import lru_cache
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from corrector import settings
from corrector.correct import Corrector
from corrector.edits import apply_edits
from corrector.llm import bounded_deepseek

app = FastAPI(
    title="Editor Agent API",
    version="0.1.0",
)


@lru_cache
def get_corrector():
    """The production corrector, built lazily so importing this module never
    reaches for a provider client. A FastAPI dependency rather than a
    module-level value so tests can override it with a fake `generate`.
    """
    return Corrector(
        model=settings.MODEL,
        generate=bounded_deepseek(settings.EFFORT),
        block_words=settings.BLOCK_WORDS,
    )


class CorrectFileRequest(BaseModel):
    file_path: str


class CorrectFileResponse(BaseModel):
    status: str
    text: str
    proposed: int
    applied: int
    skipped: int
    rejected: dict[str, int]
    errors: list[str]
    usage: dict


@app.post(
    "/correct-file",
    response_model=CorrectFileResponse,
)
def correct_file(request: CorrectFileRequest, corrector: Corrector = Depends(get_corrector)):
    path = Path(request.file_path)

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="File not found",
        )

    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="File must be UTF-8 encoded",
        )

    correction = corrector.correct(text)

    # A pass whose every call failed must not read as "no errors found" — that
    # is what an invalid key looked like before: 200, and the text handed
    # straight back. `correct` records one error per failed call and counts one
    # call per attempt, so `errors == calls` is the whole pass failing, while
    # anything less is per-block mode losing some blocks and keeping the rest.
    # That is a partial result worth returning, with the failures still in the
    # body rather than thrown away with it.
    if correction.errors and len(correction.errors) == correction.usage.calls:
        raise HTTPException(
            status_code=502,
            detail=f"correction pass failed: {'; '.join(correction.errors)}",
        )

    corrected_text, apply_rejections = apply_edits(
        text,
        correction.edits,
    )

    rejected = dict(correction.rejected)

    for rejection in apply_rejections:
        rejected[rejection.reason] = rejected.get(rejection.reason, 0) + 1

    return CorrectFileResponse(
        status="completed",
        text=corrected_text,
        proposed=correction.proposed,
        applied=len(correction.edits) - len(apply_rejections),
        skipped=sum(rejected.values()),
        rejected=rejected,
        errors=correction.errors,
        usage=correction.usage.model_dump(),
    )
