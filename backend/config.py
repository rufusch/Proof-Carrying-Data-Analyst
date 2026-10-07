import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path(os.getenv("DATA_DIR", "data")).resolve())
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", "sqlite:///data/state.db"))
    retention_hours: int = 24
    max_files: int = 10
    max_file_bytes: int = 50 * 1024 * 1024
    max_dataset_bytes: int = 200 * 1024 * 1024
    max_rows: int = 500_000
    max_columns: int = 200
    max_tables: int = 40
    max_jobs: int = 100
    sandbox_image: str = field(default_factory=lambda: os.getenv("SANDBOX_IMAGE", "proof-analyst-sandbox:local"))
    docker_executable: str = field(default_factory=lambda: os.getenv("DOCKER_EXECUTABLE", ""))
    docker_wsl_distro: str = field(default_factory=lambda: os.getenv("DOCKER_WSL_DISTRO", ""))
    sandbox_timeout: int = 90
    model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", ""))
    planner: str = field(default_factory=lambda: os.getenv("PLANNER", "deterministic"))
    cors_origins: list[str] = field(default_factory=lambda: os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","))

    def __post_init__(self):
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if self.database_url == "sqlite:///data/state.db":
            self.database_url = "sqlite:///" + (self.data_dir / "state.db").as_posix()
