"""
Git-based versioning for memory directories.

Auto-commits changes to semantic/, procedural/, and user/ directories
to provide full history and diff capability.
"""

import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class MemoryVersioning:
    """
    Manages git-based versioning of memory directories.

    Tracks changes in semantic/, procedural/, and user/ while
    excluding working/, episodic/, and attachments/.

    If git is not installed, versioning is silently disabled
    so that Archivyst can still function without it.
    """

    _git_available: bool | None = None

    def __init__(self, memory_root: Path):
        self.root = memory_root
        self._tracked_dirs = ["semantic", "procedural", "user"]

    @staticmethod
    def _check_git_available() -> bool:
        """Check whether git is installed and reachable."""
        try:
            subprocess.run(
                ["git", "--version"],
                capture_output=True,
                text=True,
                check=True,
            )
            return True
        except (FileNotFoundError, subprocess.CalledProcessError):
            return False

    def ensure_repo(self) -> None:
        """Initialize git repo in memory root if not already present."""
        # Check git availability on first call
        if MemoryVersioning._git_available is None:
            MemoryVersioning._git_available = self._check_git_available()

        if not MemoryVersioning._git_available:
            logger.warning(
                "[versioning] git is not installed; memory versioning disabled"
            )
            return

        git_dir = self.root / ".git"
        if git_dir.exists():
            return

        try:
            self._run_git("init")

            # Create .gitignore
            gitignore = self.root / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "# Only track semantic, procedural, and user directories\n"
                    "working/\n"
                    "episodic/\n"
                    "attachments/\n"
                    ".obsidian/\n"
                )
                self._run_git("add", ".gitignore")
                self._run_git("commit", "-m", "Initialize memory versioning")
        except FileNotFoundError:
            MemoryVersioning._git_available = False
            logger.warning(
                "[versioning] git binary not found; memory versioning disabled"
            )
        except subprocess.CalledProcessError as e:
            logger.warning(
                "[versioning] Git operation failed during repo init: %s",
                e.stderr.strip() if e.stderr else e,
            )

    def auto_commit(self, message: str) -> str | None:
        """
        Stage changes in tracked directories and commit if any exist.

        Returns commit hash if a commit was made, None otherwise.
        """
        if MemoryVersioning._git_available is False:
            return None

        try:
            # Stage tracked directories
            for dir_name in self._tracked_dirs:
                dir_path = self.root / dir_name
                if dir_path.exists():
                    self._run_git("add", dir_name)

            # Check if there are staged changes
            result = self._run_git("diff", "--cached", "--quiet", check=False)
            if result.returncode == 0:
                return None  # No changes

            # Sanitize commit message to prevent git argument injection
            # Strip control characters, limit length, ensure it doesn't start with "-"
            safe_message = message.replace("\n", " ").replace("\r", "")[:500]
            if safe_message.startswith("-"):
                safe_message = f" {safe_message}"
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            full_message = f"{safe_message} [{timestamp}]"
            self._run_git("commit", "-m", full_message)

            # Get commit hash
            result = self._run_git("rev-parse", "--short", "HEAD")
            return str(result.stdout.strip())
        except FileNotFoundError:
            MemoryVersioning._git_available = False
            logger.warning(
                "[versioning] git binary not found; memory versioning disabled"
            )
            return None
        except subprocess.CalledProcessError as e:
            logger.warning(
                "[versioning] Git operation failed: %s",
                e.stderr.strip() if e.stderr else e,
            )
            return None

    def get_history(
        self,
        path: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        """
        Get commit history for tracked directories.

        Args:
            path: Optional path filter (relative to memory root)
            limit: Maximum number of entries to return

        Returns:
            List of dicts with hash, date, message keys.
        """
        if MemoryVersioning._git_available is False:
            return []

        args = [
            "log",
            f"-n{limit}",
            "--format=%h|%aI|%s",
        ]
        if path:
            args.extend(["--", path])

        try:
            result = self._run_git(*args, check=False)
        except FileNotFoundError:
            MemoryVersioning._git_available = False
            return []

        if result.returncode != 0:
            return []

        entries = []
        for line in result.stdout.strip().split("\n"):
            if not line:
                continue
            parts = line.split("|", 2)
            if len(parts) == 3:
                entries.append(
                    {
                        "hash": parts[0],
                        "date": parts[1],
                        "message": parts[2],
                    }
                )
        return entries

    def diff(self, commit1: str, commit2: str = "HEAD") -> str:
        """Get diff between two commits."""
        if MemoryVersioning._git_available is False:
            return ""

        try:
            result = self._run_git(
                "diff",
                commit1,
                commit2,
                "--",
                *self._tracked_dirs,
                check=False,
            )
            return str(result.stdout)
        except FileNotFoundError:
            MemoryVersioning._git_available = False
            return ""

    def _run_git(
        self, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run a git command in the memory root directory.

        Author identity is passed via -c flags on every command to avoid
        writing to the repo config (LM2: git configuration injection).
        """
        return subprocess.run(
            [
                "git",
                "-c",
                "user.name=Archivyst Agent",
                "-c",
                "user.email=archivyst@local",
                *args,
            ],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=check,
        )
