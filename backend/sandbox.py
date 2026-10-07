import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import canonical, uid


class SandboxUnavailable(RuntimeError):
    @property
    def public_message(self):
        approved = {
            "Sandbox time limit exceeded.", "Sandbox output exceeded the limit.",
            "The isolated worker is unavailable.",
            "Isolated parsing or execution failed. Check file validity, supported operations and resource limits.",
        }
        return str(self) if str(self) in approved else "The isolated worker is unavailable or exceeded its resource limits."


class SandboxRejected(ValueError):
    pass


class DockerSandbox:
    def __init__(self, settings):
        self.settings = settings

    def executable(self):
        if self.settings.docker_executable:
            return self.settings.docker_executable
        found = shutil.which("docker")
        if found:
            return found
        if os.name == "nt":
            for directory in [Path(os.getenv("LOCALAPPDATA", "")) / "Programs/DockerDesktop/resources/bin", Path("C:/Program Files/Docker/Docker/resources/bin")]:
                candidate = directory / "docker.exe"
                if candidate.is_file():
                    return str(candidate)
        return "docker"

    def command(self, *arguments):
        if self.settings.docker_wsl_distro:
            return ["wsl.exe", "--distribution", self.settings.docker_wsl_distro, "--user", "root", "--exec", "docker", *arguments]
        return [self.executable(), *arguments]

    def input_root(self, root):
        path = str(Path(root).resolve())
        if self.settings.docker_wsl_distro:
            converted = subprocess.run(["wsl.exe", "--distribution", self.settings.docker_wsl_distro, "--user", "root", "--exec", "wslpath", "-a", "-u", path], capture_output=True, text=True, timeout=15, check=True)
            return converted.stdout.strip()
        return path

    def run(self, root, request):
        name = "analyst-" + uid()
        command = self.command("run", "--rm", "--pull=never", "--name", name,
            "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
            "--pids-limit=64", "--memory=768m", "--memory-swap=768m", "--cpus=1",
            "--user=65534:65534", "--tmpfs=/tmp:rw,noexec,nosuid,size=64m", "-i")
        if root is not None:
            command += ["--mount", f"type=bind,src={self.input_root(root)},dst=/inputs,readonly"]
        command.append(self.settings.sandbox_image)
        try:
            with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=output, stderr=errors, shell=False)
                try:
                    process.communicate(canonical(request).encode(), timeout=self.settings.sandbox_timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise SandboxUnavailable("Sandbox time limit exceeded.")
                if process.returncode:
                    raise SandboxUnavailable("Isolated parsing or execution failed. Check file validity, supported operations and resource limits.")
                if output.tell() > 128 * 1024**2:
                    raise SandboxUnavailable("Sandbox output exceeded the limit.")
                output.seek(0)
                result = json.load(output)
                if "rejected" in result:
                    raise SandboxRejected(result["rejected"])
                return result
        except (OSError, json.JSONDecodeError) as exc:
            raise SandboxUnavailable("The isolated worker is unavailable.") from exc
        finally:
            try:
                subprocess.run(self.command("rm", "-f", name), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def ready(self):
        try:
            return self.run(None, {"mode": "probe"}).get("ready") is True
        except (SandboxUnavailable, subprocess.TimeoutExpired):
            return False
