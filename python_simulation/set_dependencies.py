"""set_dependencies.py -- make the repo ready to run, in one command.

    python set_dependencies.py            # install deps, fetch data, build artifacts
    python set_dependencies.py --check    # report readiness only; change nothing

Every step is idempotent: it is skipped when its output already exists, so this
is safe to re-run. Run it with the SAME interpreter you'll run the system with
-- packages install into that environment:

    /opt/miniconda3/envs/waleed/bin/python set_dependencies.py

What "ready to run" means here:
  1. Python packages installed (polars, anthropic, flask, ...).
  2. The IBM HI-Small dataset present in the kagglehub cache.
  3. The pipeline artifacts built:
       out/eval_report.json     calibrated thresholds  (python -m system.engine)
       out/alerts.parquet       batch alert sample
       out/alerts_stream.jsonl  the agent's inbox      (python run_system.py)
     (out/features_compact.parquet builds itself on the first server/agent run.)
  4. ANTHROPIC_API_KEY available (from .env) for the agent and the live UI.
"""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent
OUT = REPO / "out"

# (import name, pip name) -- flask_cors installs as Flask-Cors, etc.
PACKAGES = [
    ("polars", "polars"),
    ("pydantic", "pydantic"),
    ("anthropic", "anthropic"),
    ("flask", "Flask"),
    ("flask_cors", "Flask-Cors"),
    ("openai", "openai"),          # only the Groq/OpenAI-compatible path needs it
    ("kagglehub", "kagglehub"),    # to fetch the dataset
]

DATASET = "ealtman2019/ibm-transactions-for-anti-money-laundering-aml"
DATA_DIR = (Path("~/.cache/kagglehub/datasets").expanduser()
            / "ealtman2019/ibm-transactions-for-anti-money-laundering-aml/versions/8")
TRANS = DATA_DIR / "HI-Small_Trans.csv"
PATTERNS = DATA_DIR / "HI-Small_Patterns.txt"

OK, MISS = "✓", "✗"          # ✓ / ✗


def missing_packages() -> list[tuple[str, str]]:
    return [p for p in PACKAGES if importlib.util.find_spec(p[0]) is None]


def install_packages(check_only: bool) -> bool:
    missing = missing_packages()
    if not missing:
        print(f"  {OK} all {len(PACKAGES)} packages present")
        return True
    names = ", ".join(imp for imp, _ in missing)
    if check_only:
        print(f"  {MISS} missing: {names}")
        return False
    print(f"  installing: {names}")
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet",
                    *[pip for _, pip in missing]], check=True)
    print(f"  {OK} installed {len(missing)} package(s)")
    return True


def fetch_dataset(check_only: bool) -> bool:
    if TRANS.exists() and PATTERNS.exists():
        print(f"  {OK} dataset present ({DATA_DIR})")
        return True
    if check_only:
        print(f"  {MISS} dataset absent -- run without --check to download (~2GB)")
        return False
    print(f"  downloading {DATASET} via kagglehub (~2GB, one-time)...")
    import kagglehub
    path = kagglehub.dataset_download(DATASET)
    if not TRANS.exists():
        print(f"  {MISS} downloaded to {path} but {TRANS.name} not found there; "
              "check the dataset version.")
        return False
    print(f"  {OK} dataset ready ({DATA_DIR})")
    return True


def _run(cmd: list[str], label: str) -> bool:
    print(f"  building {label} ... ({' '.join(cmd[1:])})")
    r = subprocess.run(cmd, cwd=REPO)
    return r.returncode == 0


def build_artifacts(check_only: bool) -> bool:
    report = OUT / "eval_report.json"
    stream = OUT / "alerts_stream.jsonl"
    have_thr, have_stream = report.exists(), stream.exists()
    if have_thr and have_stream:
        print(f"  {OK} artifacts present (eval_report.json, alerts_stream.jsonl)")
        return True
    if check_only:
        if not have_thr:
            print(f"  {MISS} out/eval_report.json  (python -m system.engine)")
        if not have_stream:
            print(f"  {MISS} out/alerts_stream.jsonl  (python run_system.py)")
        return False
    if not TRANS.exists():
        print(f"  {MISS} cannot build -- dataset missing (fetch it first)")
        return False
    # thresholds first: run_system.py loads them to score the stream.
    if not have_thr:
        if not _run([sys.executable, "-m", "system.engine",
                     str(TRANS), str(PATTERNS)], "eval_report.json + alerts.parquet"):
            return False
    if not have_stream:
        if not _run([sys.executable, "run_system.py", str(TRANS), str(PATTERNS)],
                    "alerts_stream.jsonl"):
            return False
    print(f"  {OK} artifacts built")
    return True


def check_api_key() -> bool:
    import os
    env = REPO / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("\"'"))
    if os.environ.get("ANTHROPIC_API_KEY"):
        print(f"  {OK} ANTHROPIC_API_KEY found")
        return True
    print(f"  {MISS} ANTHROPIC_API_KEY not set -- add it to .env "
          "(ANTHROPIC_API_KEY=sk-ant-...) for the agent and live UI")
    return False


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true",
                    help="report readiness only; install/download/build nothing")
    args = ap.parse_args()

    print(f"interpreter: {sys.executable}\n")
    steps = [
        ("1. Python packages", lambda: install_packages(args.check)),
        ("2. IBM HI-Small dataset", lambda: fetch_dataset(args.check)),
        ("3. Pipeline artifacts", lambda: build_artifacts(args.check)),
        ("4. API key", check_api_key),
    ]
    results = []
    for title, fn in steps:
        print(title)
        try:
            results.append(fn())
        except Exception as e:                                 # noqa: BLE001
            print(f"  {MISS} {type(e).__name__}: {e}")
            results.append(False)
        print()

    ready = all(results)
    print("=" * 60)
    if ready:
        print(f"{OK} READY. Run any of:")
        print("    python server.py                       # live UI -> localhost:8000")
        print("    python run_agent.py --limit 20         # batch: alerts -> cases")
        print("    python simulate.py                     # CLI day-replay")
    elif args.check:
        print("Not ready. Re-run WITHOUT --check to install/build the missing pieces.")
    else:
        print("Some steps did not complete -- see the ✗ lines above.")
    sys.exit(0 if ready else 1)


if __name__ == "__main__":
    main()
