"""Build using a minimal tar context, avoiding Windows/WSL xattr and ACL issues."""
import io
from pathlib import Path
import subprocess
import sys
import tarfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.config import Settings
from backend.sandbox import DockerSandbox


def context():
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        files = [ROOT / "Dockerfile.sandbox", ROOT / "requirements.lock", *sorted((ROOT / "backend").glob("*.py")), ROOT / "backend/intent_model.json"]
        for path in files:
            content = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(ROOT).as_posix())
            info.size, info.mode = len(content), 0o644
            archive.addfile(info, io.BytesIO(content))
    return buffer.getvalue()


if __name__ == "__main__":
    settings = Settings()
    result = subprocess.run(DockerSandbox(settings).command("build", "-f", "Dockerfile.sandbox", "-t", settings.sandbox_image, "-"), input=context())
    raise SystemExit(result.returncode)
