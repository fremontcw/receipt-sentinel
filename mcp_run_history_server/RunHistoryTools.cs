/// # RunHistoryTools
/// The MCP tools an agent can call: list recent eval runs, and ask what regressed in a run
/// compared with the blessed baseline. Read-only; no receipt text is returned.
///
/// ## Status
/// None
///
/// ## Issues
/// None
///
/// ## Todo
/// None

using System.ComponentModel;
using ModelContextProtocol;
using ModelContextProtocol.Server;

namespace RunHistoryMcpServer;

[McpServerToolType]
public static class RunHistoryTools
{
    [McpServerTool(Name = "list_runs", ReadOnly = true, Idempotent = true)]
    [Description("List the most recent Receipt Sentinel eval runs, newest first, with pass rate, model, prompt and cost. Use what_regressed for the verdict.")]
    public static IReadOnlyList<RunSummary> ListRuns(
        RunHistoryStore store,
        [Description("How many runs to return (1-100).")] int limit = 10) =>
        store.ListRuns(limit);

    [McpServerTool(Name = "what_regressed", ReadOnly = true, Idempotent = true)]
    [Description("Compare an eval run with the blessed baseline: which cases newly fail and why, and which newly pass.")]
    public static RegressionReport WhatRegressed(
        RunHistoryStore store,
        [Description("Run id such as 20261017T132229Z-10086e. Omit for the newest run.")] string? runId = null)
    {
        try
        {
            return store.CompareToBaseline(runId);
        }
        catch (Exception err) when (err is ArgumentException or FileNotFoundException or InvalidOperationException)
        {
            // Expected user errors go back to the agent as a readable tool error, not a crash.
            throw new McpException(err.Message);
        }
    }
}
