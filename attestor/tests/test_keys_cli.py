import os
import subprocess
import sys
from pathlib import Path

from eth_utils import keccak

REPO = Path(__file__).resolve().parents[2]


def run_cli(data_dir: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATA_DIR": str(data_dir), "PYTHONPATH": str(REPO)}
    return subprocess.run([sys.executable, "-m", "attestor.keys"], env=env, cwd=REPO, capture_output=True, text=True,
                          timeout=120)


def test_keys_cli_prints_only_the_hash_and_is_idempotent(tmp_path):
    first = run_cli(tmp_path)
    assert first.returncode == 0, first.stderr
    out = first.stdout
    assert out.count("\n") == 1 and out.endswith("\n")  # exactly one line on stdout
    h = out.strip()
    assert h.startswith("0x") and len(h) == 66 and h == h.lower()
    pk = (tmp_path / "keys" / "mldsa44.pk").read_bytes()  # created under DATA_DIR, same place the service uses
    assert len(pk) == 1312 and h == "0x" + keccak(pk).hex()
    assert run_cli(tmp_path).stdout.strip() == h  # second call reuses the key


def test_service_loads_the_key_the_cli_created(tmp_path):
    from attestor.signing import MLDSASigner

    h = run_cli(tmp_path).stdout.strip()
    signer = MLDSASigner.load_or_create(tmp_path / "keys")
    assert "0x" + keccak(signer.public_key).hex() == h
