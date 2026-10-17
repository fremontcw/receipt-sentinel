/// # Program
/// Entry point for the Receipt Sentinel run-history MCP server (stdio transport).
/// Reads SENTINEL_RUNS_DIR and SENTINEL_BASELINE, then serves read-only tools.
///
/// ## Status
/// None
///
/// ## Issues
/// None
///
/// ## Todo
/// None

using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;
using Microsoft.Extensions.Logging;
using RunHistoryMcpServer;

var runsDir = Environment.GetEnvironmentVariable("SENTINEL_RUNS_DIR");
var baselinePath = Environment.GetEnvironmentVariable("SENTINEL_BASELINE");
if (string.IsNullOrWhiteSpace(runsDir) || string.IsNullOrWhiteSpace(baselinePath))
{
    Console.Error.WriteLine(
        "Set SENTINEL_RUNS_DIR (folder of run JSON files) and SENTINEL_BASELINE (baseline JSON).");
    return 2;
}

var builder = Host.CreateApplicationBuilder(args);
// stdout carries the MCP protocol, so every log line must go to stderr.
builder.Logging.AddConsole(options => options.LogToStandardErrorThreshold = LogLevel.Trace);
builder.Services.AddSingleton(new RunHistoryStore(runsDir, baselinePath));
builder.Services.AddMcpServer().WithStdioServerTransport().WithToolsFromAssembly();
await builder.Build().RunAsync();
return 0;
