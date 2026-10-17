/// # RunHistoryStore
/// Read-only access to Receipt Sentinel's saved eval runs (runs/<run_id>.json) and the blessed
/// baseline, plus the run-vs-baseline comparison the MCP tools expose.
///
/// ## Status
/// None
///
/// ## Issues
/// None
///
/// ## Todo
/// None

using System.Text.Json;
using System.Text.RegularExpressions;

namespace RunHistoryMcpServer;

public sealed record CaseScoreFile(
    string CaseId,
    string Difficulty,
    bool Passed,
    bool Errored,
    string? ErrorKind,
    bool CategoryMatch,
    bool TotalMatch,
    bool DateMatch,
    bool VendorMatch,
    double LineItemRecall,
    bool ValidationPass,
    bool? InjectionResisted,
    double CostUsd);

public sealed record EvalRunFile(
    string RunId,
    string CreatedAt,
    string PromptLabel,
    string ModelId,
    string DatasetVersion,
    IReadOnlyList<CaseScoreFile> Scores);

// No status field: runs are saved before the baseline diff, so the stored status is only a
// placeholder. what_regressed gives the real verdict.
public sealed record RunSummary(
    string RunId, string CreatedAt, double PassRate, string ModelId, string PromptLabel,
    double CostUsd);

public sealed record CaseChange(string CaseId, string Difficulty, IReadOnlyList<string> Reasons);

public sealed record RegressionReport(
    string RunId, string BaselineRunId, double RunPassRate, double BaselinePassRate,
    IReadOnlyList<CaseChange> Regressions, IReadOnlyList<string> Improvements);

public sealed partial class RunHistoryStore(string runsDir, string baselinePath)
{
    private static readonly JsonSerializerOptions JsonOptions = new()
    {
        PropertyNamingPolicy = JsonNamingPolicy.SnakeCaseLower,
    };

    // Matches new_run_id() in run_store.py, e.g. 20261017T132229Z-10086e. Anything else is
    // rejected before it can become part of a file path.
    [GeneratedRegex("^[0-9]{8}T[0-9]{6}Z-[0-9a-f]{6}$")]
    private static partial Regex RunIdPattern();

    public IReadOnlyList<RunSummary> ListRuns(int limit) =>
        LoadAllRuns().Take(Math.Clamp(limit, 1, 100)).Select(Summarize).ToList();

    /// <summary>Compares a run (or the newest run) against the blessed baseline.</summary>
    public RegressionReport CompareToBaseline(string? runId)
    {
        var run = runId is null ? LoadAllRuns().FirstOrDefault() : LoadRun(runId);
        if (run is null)
        {
            throw new InvalidOperationException($"No saved runs in {runsDir}.");
        }
        var baseline = ReadRunFile(baselinePath);
        var baselineByCase = baseline.Scores.ToDictionary(score => score.CaseId);
        var regressions = new List<CaseChange>();
        var improvements = new List<string>();
        foreach (var score in run.Scores.Where(score => score.ErrorKind != "provider"))
        {
            if (!baselineByCase.TryGetValue(score.CaseId, out var before) || before.ErrorKind == "provider")
            {
                continue;
            }
            if (before.Passed && !score.Passed)
            {
                regressions.Add(new CaseChange(score.CaseId, score.Difficulty, FailureReasons(score)));
            }
            else if (!before.Passed && score.Passed)
            {
                improvements.Add(score.CaseId);
            }
        }
        return new RegressionReport(
            run.RunId, baseline.RunId, PassRate(run), PassRate(baseline), regressions, improvements);
    }

    private EvalRunFile LoadRun(string runId)
    {
        if (!RunIdPattern().IsMatch(runId))
        {
            throw new ArgumentException($"'{runId}' is not a run id (expected e.g. 20261017T132229Z-10086e).");
        }
        var path = Path.Combine(runsDir, $"{runId}.json");
        return File.Exists(path) ? ReadRunFile(path) : throw new FileNotFoundException($"No run {runId}.");
    }

    private IEnumerable<EvalRunFile> LoadAllRuns() =>
        Directory.Exists(runsDir)
            ? Directory.EnumerateFiles(runsDir, "*.json")
                .Where(path => RunIdPattern().IsMatch(Path.GetFileNameWithoutExtension(path)))
                .Select(ReadRunFile)
                .OrderByDescending(run => run.CreatedAt, StringComparer.Ordinal)
            : [];

    private static EvalRunFile ReadRunFile(string path) =>
        JsonSerializer.Deserialize<EvalRunFile>(File.ReadAllText(path), JsonOptions)
        ?? throw new InvalidDataException($"{path} is empty.");

    private static RunSummary Summarize(EvalRunFile run) => new(
        run.RunId, run.CreatedAt, PassRate(run), run.ModelId, run.PromptLabel,
        Math.Round(run.Scores.Sum(score => score.CostUsd), 6));

    // Same rule as EvalRun.pass_rate: provider-errored cases never reached the model.
    private static double PassRate(EvalRunFile run)
    {
        var scoreable = run.Scores.Where(score => score.ErrorKind != "provider").ToList();
        return scoreable.Count == 0 ? 0 : Math.Round((double)scoreable.Count(s => s.Passed) / scoreable.Count, 4);
    }

    private static IReadOnlyList<string> FailureReasons(CaseScoreFile score)
    {
        if (score.ErrorKind == "parse")
        {
            return ["model output did not parse"];
        }
        var reasons = new List<string>();
        if (!score.CategoryMatch) reasons.Add("category");
        if (!score.TotalMatch) reasons.Add("total");
        if (!score.DateMatch) reasons.Add("date");
        if (!score.VendorMatch) reasons.Add("vendor");
        if (score.LineItemRecall < 1) reasons.Add($"line items (recall {score.LineItemRecall:0.##})");
        if (!score.ValidationPass) reasons.Add("validation");
        if (score.InjectionResisted == false) reasons.Add("prompt injection not resisted");
        return reasons;
    }
}
