"""Export built UI assets from the exact local runtime image for browser checks."""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path


def docker(arguments: tuple[str, ...]) -> str:
    result = subprocess.run(  # noqa: S603 -- fixed local artifact-export arguments.
        ("docker",) + arguments, capture_output=True, text=True, timeout=60, check=True
    )
    return result.stdout.strip()


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    target = root / "frontend/dist"
    if target.is_symlink():
        raise RuntimeError("Refusing symlink asset output")
    image = json.loads(docker(("image", "inspect", "mail-admin:candidate")))[0]
    identifier = docker(
        ("create", "--platform", "linux/amd64", "--label", "mail-admin.fixture=true", image["Id"])
    )
    try:
        if target.exists():
            shutil.rmtree(target)
        target.mkdir()
        docker(("cp", identifier + ":/app/static/.", str(target)))
    finally:
        docker(("rm", identifier))
    files = {
        str(path.relative_to(target)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(target.rglob("*"))
        if path.is_file()
    }
    (root / "build/frontend-export.json").write_text(
        json.dumps({"image_id": image["Id"], "files": files}, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
