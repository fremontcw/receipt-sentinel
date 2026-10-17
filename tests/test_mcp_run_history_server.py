"""End-to-end check of the C# run-history MCP server over stdio JSON-RPC.

Skipped unless the server has been built (`dotnet build mcp_run_history_server`).
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from receipt_sentinel.evals.eval_commands import EvalPaths, command_evals_run

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_DLL = REPO_ROOT / "mcp_run_history_server/bin/Debug/net10.0/RunHistoryMcpServer.dll"
DOTNET = shutil.which("dotnet") or str(Path.home() / ".dotnet/dotnet")

pytestmark = pytest.mark.skipif(
    not SERVER_DLL.exists() or not Path(DOTNET).exists(), reason="MCP server not built"
)


def _rpc(requests: list[dict[str, object]], env: dict[str, str]) -> dict[int, dict[str, object]]:
    """Send requests while keeping stdin open (the server stops at EOF), collect replies by id."""
    server = subprocess.Popen(  # noqa: S603 - fixed argv, no shell
        [DOTNET, str(SERVER_DLL)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL, text=True, env=env,
    )
    assert server.stdin and server.stdout
    replies: dict[int, dict[str, object]] = {}
    try:
        for request in requests:
            server.stdin.write(json.dumps(request) + "\n")
            server.stdin.flush()
            if "id" in request:
                reply = json.loads(server.stdout.readline())
                replies[reply["id"]] = reply
    finally:
        server.stdin.close()
        server.wait(timeout=30)
    return replies


def _call(request_id: int, tool: str, arguments: dict[str, object]) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": request_id, "method": "tools/call",
            "params": {"name": tool, "arguments": arguments}}


def test_server_lists_runs_and_reports_the_demo_regression(tmp_path: Path) -> None:
    paths = EvalPaths.default(tmp_path, fake=True)
    command_evals_run(paths, fake=True, save=True, slack_webhook=None, report_url=None,
                      demo_noise=True)
    env = {**os.environ, "SENTINEL_RUNS_DIR": str(paths.runs_dir),
           "SENTINEL_BASELINE": str(paths.baseline_path)}
    replies = _rpc([
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "1"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        _call(3, "what_regressed", {}),
        _call(4, "what_regressed", {"runId": "../../etc/passwd"}),
    ], env)

    tools = {t["name"] for t in replies[2]["result"]["tools"]}  # type: ignore[index]
    assert tools == {"list_runs", "what_regressed"}

    report = json.loads(replies[3]["result"]["content"][0]["text"])  # type: ignore[index]
    assert report["regressions"], "demo noise should cause at least one regression"
    assert all("category" in r["reasons"] for r in report["regressions"])

    # A path-like run id is refused as a tool error, never read from disk.
    assert replies[4]["result"]["isError"] is True  # type: ignore[index]
