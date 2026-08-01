from __future__ import annotations

import os
import sys
from pathlib import Path

# Project root (ai-director)
PROJECT_ROOT = Path(__file__).resolve().parents[2]

# Get external repository base directories
MAIL_DIR_ENV = os.environ.get("AI_MAIL_PATH")
ORCHESTRATOR_DIR_ENV = os.environ.get("AI_ORCHESTRATOR_PATH")

def resolve_dir(env_val: str | None, repo_name: str, fallback_names: list[str]) -> Path:
    if env_val:
        p = Path(env_val).resolve()
        if p.exists():
            return p
        raise ImportError(
            f"環境変数で指定された {repo_name} のパス '{env_val}' が存在しません。"
        )

    # Search in adjacent workspace directory
    for name in [repo_name] + fallback_names:
        p = PROJECT_ROOT.parent / name
        if p.exists():
            return p.resolve()

    raise ImportError(
        f"{repo_name} リポジトリが見つかりません。\n"
        f"環境変数 {'AI_MAIL_PATH' if repo_name == 'aiagent-mail' else 'AI_ORCHESTRATOR_PATH'} に絶対パスを指定するか、"
        f"または {PROJECT_ROOT.parent} の直下に '{repo_name}' リポジトリを配置してください。"
    )

try:
    MAIL_PATH = resolve_dir(MAIL_DIR_ENV, "aiagent-mail", ["mail"])
    ORCHESTRATOR_PATH = resolve_dir(ORCHESTRATOR_DIR_ENV, "ai-orchestrator", ["orchestrator"])
except ImportError as exc:
    print(f"\n[Test Environment Error]: {exc}\n", file=sys.stderr)
    sys.exit(1)

# Add paths to sys.path to enable imports
if str(MAIL_PATH) not in sys.path:
    sys.path.insert(0, str(MAIL_PATH))
if str(MAIL_PATH / "mail") not in sys.path:
    sys.path.insert(0, str(MAIL_PATH / "mail"))
if str(ORCHESTRATOR_PATH) not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_PATH))
if str(ORCHESTRATOR_PATH / "orchestrator") not in sys.path:
    sys.path.insert(0, str(ORCHESTRATOR_PATH / "orchestrator"))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Strict assertion to ensure we import modules from our resolved paths and not pre-installed packages
try:
    import mail
    resolved_mail_file = Path(mail.__file__).resolve()
    expected_mail_path = (MAIL_PATH / "mail").resolve()
    if expected_mail_path not in resolved_mail_file.parents:
        raise ImportError(
            f"想定外の 'mail' モジュールがインポートされました。\n"
            f"インポートされたファイル: {resolved_mail_file}\n"
            f"期待されるパス配下: {expected_mail_path}"
        )
except ImportError as e:
    if "想定外の" in str(e):
        print(f"\n[Test Environment Error]: {e}\n", file=sys.stderr)
        sys.exit(1)

try:
    import config
    resolved_config_file = Path(config.__file__).resolve()
    expected_orchestrator_path = (ORCHESTRATOR_PATH / "orchestrator").resolve()
    if expected_orchestrator_path not in resolved_config_file.parents:
        raise ImportError(
            f"想定外の 'config' モジュールがインポートされました。\n"
            f"インポートされたファイル: {resolved_config_file}\n"
            f"期待されるパス配下: {expected_orchestrator_path}"
        )
except ImportError as e:
    if "想定外の" in str(e):
        print(f"\n[Test Environment Error]: {e}\n", file=sys.stderr)
        sys.exit(1)
