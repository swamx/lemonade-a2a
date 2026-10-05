// Independent-client interoperability check using the official A2A .NET SDK
// (NuGet "A2A", currently a 1.0.0 preview).
//
//   cd interop/dotnet && dotnet run -- [baseUrl] [--auth <key>]
//
// Runs the same scenarios as ../js_client.mjs over JSON-RPC (A2AClient) and
// HTTP+JSON (A2AHttpJsonClient); exit code is non-zero if any scenario fails.
using System.Net.Http.Headers;
using A2A;

var arguments = args.ToList();
string? auth = null;
var authIndex = arguments.IndexOf("--auth");
if (authIndex >= 0)
{
    auth = arguments[authIndex + 1];
    arguments.RemoveRange(authIndex, 2);
}
var baseUrl = new Uri(arguments.FirstOrDefault() ?? "http://127.0.0.1:9100/");

var http = new HttpClient { Timeout = TimeSpan.FromMinutes(5) };
if (auth is not null)
{
    http.DefaultRequestHeaders.Authorization = new AuthenticationHeaderValue("Bearer", auth);
}
http.DefaultRequestHeaders.Add("A2A-Version", "1.0");

SendMessageRequest User(string text) => new()
{
    Message = new Message
    {
        MessageId = Guid.NewGuid().ToString("N"),
        Role = Role.User,
        Parts = [Part.FromText(text)],
    },
};

string ArtifactText(AgentTask task) =>
    string.Concat((task.Artifacts ?? []).SelectMany(a => a.Parts).Select(p => p.Text ?? ""));

var results = new List<bool>();

async Task Check(string binding, string name, Func<Task<string>> scenario)
{
    try
    {
        var detail = await scenario();
        results.Add(true);
        Console.WriteLine($"PASS {binding} {name} - {detail}");
    }
    catch (Exception error)
    {
        results.Add(false);
        Console.WriteLine($"FAIL {binding} {name} - {error.GetType().Name}: {error.Message}");
    }
}

var clients = new (string Binding, IA2AClient Client)[]
{
    ("JSONRPC", new A2AClient(baseUrl, http)),
    ("HTTP+JSON", new A2AHttpJsonClient(baseUrl, http)),
};

foreach (var (binding, client) in clients)
{
    await Check(binding, "send message", async () =>
    {
        var response = await client.SendMessageAsync(User("hello"));
        var task = response.Task ?? throw new Exception("expected a task");
        if (task.Status.State != TaskState.Completed) throw new Exception($"state {task.Status.State}");
        var text = ArtifactText(task);
        if (string.IsNullOrWhiteSpace(text)) throw new Exception("no artifact text");
        return $"completed, {text.Length} chars";
    });

    await Check(binding, "streaming", async () =>
    {
        var chunks = 0;
        TaskState? final = null;
        await foreach (var update in client.SendStreamingMessageAsync(User("hello")))
        {
            if (update.ArtifactUpdate is not null) chunks++;
            if (update.StatusUpdate is not null) final = update.StatusUpdate.Status.State;
        }
        if (chunks < 2) throw new Exception($"only {chunks} artifact chunks");
        if (final != TaskState.Completed) throw new Exception($"final state {final}");
        return $"{chunks} artifact chunks, completed";
    });

    await Check(binding, "get / list task", async () =>
    {
        var sent = (await client.SendMessageAsync(User("hello"))).Task ?? throw new Exception("no task");
        var fetched = await client.GetTaskAsync(new GetTaskRequest { Id = sent.Id });
        if (fetched.Id != sent.Id) throw new Exception("getTask returned another task");
        var listed = await client.ListTasksAsync(new ListTasksRequest());
        if (!listed.Tasks.Any(t => t.Id == sent.Id)) throw new Exception("task missing from listTasks");
        return "found in list";
    });

    await Check(binding, "cancel running task", async () =>
    {
        string? taskId = null;
        await foreach (var update in client.SendStreamingMessageAsync(User("long running")))
        {
            taskId ??= update.Task?.Id ?? update.StatusUpdate?.TaskId;
            if (update.ArtifactUpdate is not null) break;
        }
        var canceled = await client.CancelTaskAsync(new CancelTaskRequest { Id = taskId! });
        if (canceled.Status.State != TaskState.Canceled) throw new Exception($"state {canceled.Status.State}");
        return "TASK_STATE_CANCELED";
    });

    await Check(binding, "unknown task is an error", async () =>
    {
        try
        {
            await client.GetTaskAsync(new GetTaskRequest { Id = $"missing-{Guid.NewGuid():N}" });
        }
        catch (Exception error)
        {
            return $"rejected ({error.GetType().Name})";
        }
        throw new Exception("no error for unknown task");
    });
}

var failed = results.Count(ok => !ok);
Console.WriteLine($"\n{results.Count - failed}/{results.Count} passed");
return failed == 0 ? 0 : 1;
