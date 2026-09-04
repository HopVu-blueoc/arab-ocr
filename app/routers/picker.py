import subprocess
import sys

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/picker", tags=["picker"])

# Native macOS dialogs via osascript. This app runs on localhost for a single
# local reviewer, so shelling out to the OS's own file picker is simpler and
# more honest than reimplementing one in the browser (which cannot resolve an
# absolute filesystem path anyway - only a fake path or an uploaded blob).
_PROMPTS = {
    "folder": 'POSIX path of (choose folder with prompt "Choose an image folder")',
    "file": 'POSIX path of (choose file with prompt "Choose an image" '
    'of type {"public.png","public.jpeg","public.tiff","public.image"})',
}


def _run_osascript(script: str) -> str | None:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    if result.returncode != 0:
        if "User canceled" in result.stderr:
            return None
        raise RuntimeError(result.stderr.strip() or "osascript failed")
    return result.stdout.strip()


@router.post("/{kind}")
def pick(kind: str) -> dict[str, str | None]:
    if kind not in _PROMPTS:
        raise HTTPException(status_code=400, detail="kind must be 'folder' or 'file'")
    if sys.platform != "darwin":
        raise HTTPException(
            status_code=501, detail="native picker is only implemented for macOS"
        )
    try:
        path = _run_osascript(_PROMPTS[kind])
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from None
    return {"path": path}
