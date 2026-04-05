import subprocess
import sys
import logging
import time
import tempfile
from pathlib import Path
from core.config import config

log = logging.getLogger("sandbox")


class SandboxError(Exception):
    pass


def validate_syntax(code: str) -> str | None:
    """Validate Python syntax using compile(). Returns error string or None if valid."""
    try:
        compile(code, "<generated>", "exec")
        return None
    except SyntaxError as e:
        msg = f"SyntaxError at line {e.lineno}: {e.msg}"
        if e.text:
            msg += f"\n  Code: {e.text.strip()}"
        log.error("[SANDBOX] Syntax validation failed: %s", msg)
        return msg


def execute(code: str, expected_output: str) -> str:
    """Execute code in sandbox. Returns path to expected output file.

    Raises SandboxError on failure.
    """
    result = execute_detailed(code, expected_output)
    if not result["success"]:
        raise SandboxError(result["error"])
    return expected_output


def execute_detailed(code: str, expected_output: str) -> dict:
    """Execute code in sandbox and return detailed results.

    Returns dict with: success, stdout, stderr, error, files_generated, elapsed_ms
    """
    log.info("=" * 60)
    log.info("[SANDBOX] Starting code execution")
    log.info("[SANDBOX] Expected output: %s", expected_output)
    log.info("[SANDBOX] Code size: %d chars, %d lines", len(code), code.count("\n") + 1)

    generated_dir = Path(config.generated_dir).resolve()
    generated_dir.mkdir(parents=True, exist_ok=True)

    # Snapshot existing files before execution
    files_before = set()
    if generated_dir.is_dir():
        files_before = {str(f) for f in generated_dir.iterdir() if f.is_file()}

    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", dir=str(generated_dir),
        delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        script_path = f.name

    log.info("[SANDBOX] Temp script: %s", script_path)
    log.info("[SANDBOX] Timeout: %ds", config.sandbox_timeout)
    log.info("[SANDBOX] Running...")

    try:
        t0 = time.time()
        proc = subprocess.run(
            [sys.executable, script_path],
            capture_output=True,
            text=True,
            timeout=config.sandbox_timeout,
            cwd=str(generated_dir),
        )
        elapsed = time.time() - t0
        elapsed_ms = int(elapsed * 1000)

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""

        if stdout:
            for line in stdout.strip().split("\n")[:10]:
                log.info("[SANDBOX] stdout: %s", line)
        if stderr:
            for line in stderr.strip().split("\n")[:10]:
                log.warning("[SANDBOX] stderr: %s", line)

        # Discover files created during execution
        files_after = set()
        if generated_dir.is_dir():
            files_after = {str(f) for f in generated_dir.iterdir() if f.is_file()}
        new_files = sorted(files_after - files_before - {script_path})

        if proc.returncode != 0:
            log.error("[SANDBOX] Script FAILED (exit code %d) after %.1fs", proc.returncode, elapsed)
            return {
                "success": False,
                "stdout": stdout,
                "stderr": stderr,
                "error": f"Script failed (exit {proc.returncode}): {stderr[-500:]}",
                "files_generated": new_files,
                "elapsed_ms": elapsed_ms,
            }

        output_exists = Path(expected_output).exists()
        if not output_exists:
            log.error("[SANDBOX] Output file NOT FOUND: %s", expected_output)
            return {
                "success": False,
                "stdout": stdout,
                "stderr": stderr,
                "error": f"Output file not created: {expected_output}",
                "files_generated": new_files,
                "elapsed_ms": elapsed_ms,
            }

        file_size = Path(expected_output).stat().st_size
        log.info("[SANDBOX] SUCCESS in %.1fs — output: %s (%d bytes), %d new files",
                 elapsed, expected_output, file_size, len(new_files))
        return {
            "success": True,
            "stdout": stdout,
            "stderr": stderr,
            "error": None,
            "files_generated": new_files,
            "elapsed_ms": elapsed_ms,
        }

    except subprocess.TimeoutExpired:
        log.error("[SANDBOX] TIMEOUT after %ds", config.sandbox_timeout)
        return {
            "success": False,
            "stdout": "",
            "stderr": "",
            "error": f"Script timed out after {config.sandbox_timeout}s",
            "files_generated": [],
            "elapsed_ms": config.sandbox_timeout * 1000,
        }
    finally:
        try:
            Path(script_path).unlink()
        except OSError:
            pass
