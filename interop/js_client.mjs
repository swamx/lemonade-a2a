// Independent-client interoperability check using the official A2A JavaScript SDK.
//
//   cd interop && npm install && node js_client.mjs [baseUrl] [--auth <key>]
//
// Runs the same scenarios over JSON-RPC and HTTP+JSON against a live adapter and
// exits non-zero if any fails. The backend should be slow enough (a few seconds)
// that the "cancel" scenario catches the task while it is running.
import { randomUUID } from "node:crypto";
import { Role, TaskState } from "@a2a-js/sdk";
import { ClientFactory, ClientFactoryOptions, JsonRpcTransportFactory, RestTransportFactory } from "@a2a-js/sdk/client";

const args = process.argv.slice(2);
const authIndex = args.indexOf("--auth");
const auth = authIndex >= 0 ? args.splice(authIndex, 2)[1] : undefined;
const baseUrl = args[0] ?? "http://127.0.0.1:9100";
const serviceParameters = auth ? { Authorization: `Bearer ${auth}` } : undefined;

const message = (text) => ({
  messageId: randomUUID(),
  contextId: "",
  taskId: "",
  role: Role.ROLE_USER,
  parts: [{ content: { $case: "text", value: text } }],
  metadata: undefined,
  extensions: [],
  referenceTaskIds: [],
});
const request = (text) => ({ tenant: "", message: message(text), configuration: undefined, metadata: undefined });
// sendMessage resolves to the Task (or Message) itself, not a wrapper.
const taskOf = (response) => (response?.status ? response : undefined);
const artifactText = (task) => (task?.artifacts ?? []).flatMap((a) => a.parts).map((p) => p.content?.value ?? "").join("");

const results = [];
async function check(transport, name, fn) {
  try {
    const detail = await fn();
    results.push({ transport, name, ok: true, detail });
    console.log(`PASS ${transport} ${name}${detail ? ` - ${detail}` : ""}`);
  } catch (error) {
    results.push({ transport, name, ok: false, detail: String(error) });
    console.log(`FAIL ${transport} ${name} - ${error}`);
  }
}

for (const protocol of ["JSONRPC", "HTTP+JSON"]) {
  const options = ClientFactoryOptions.createFrom(ClientFactoryOptions.default, {
    transports: [new JsonRpcTransportFactory(), new RestTransportFactory()],
    preferredTransports: [protocol],
  });
  const client = await new ClientFactory(options).createFromUrl(baseUrl);
  const opts = { serviceParameters };

  await check(protocol, "send message", async () => {
    const task = taskOf(await client.sendMessage(request("hello"), opts));
    if (task?.status?.state !== TaskState.TASK_STATE_COMPLETED) throw new Error(`state ${task?.status?.state}`);
    if (!artifactText(task).trim()) throw new Error("no artifact text");
    return `completed, ${artifactText(task).length} chars`;
  });

  await check(protocol, "streaming", async () => {
    let chunks = 0;
    let final;
    for await (const event of client.sendMessageStream(request("hello"), opts)) {
      if (event.payload?.$case === "artifactUpdate") chunks += 1;
      if (event.payload?.$case === "statusUpdate") final = event.payload.value.status?.state;
    }
    if (chunks < 2) throw new Error(`only ${chunks} artifact chunks`);
    if (final !== TaskState.TASK_STATE_COMPLETED) throw new Error(`final state ${final}`);
    return `${chunks} artifact chunks, completed`;
  });

  let taskId;
  await check(protocol, "get / list task", async () => {
    taskId = taskOf(await client.sendMessage(request("hello"), opts))?.id;
    const fetched = await client.getTask({ tenant: "", id: taskId, historyLength: undefined }, opts);
    if (fetched.id !== taskId) throw new Error("getTask returned another task");
    const listed = await client.listTasks({ tenant: "", contextId: "", status: 0, pageSize: 50, pageToken: "", historyLength: undefined, statusTimestampAfter: undefined, includeArtifacts: false }, opts);
    if (!listed.tasks.some((t) => t.id === taskId)) throw new Error("task missing from listTasks");
    return "found in list";
  });

  await check(protocol, "cancel running task", async () => {
    let id;
    const stream = client.sendMessageStream(request("long running"), opts);
    for await (const event of stream) {
      id = id ?? event.payload?.value?.id ?? event.payload?.value?.taskId;
      if (event.payload?.$case === "artifactUpdate") break;
    }
    const canceled = await client.cancelTask({ tenant: "", id, metadata: undefined }, opts);
    if (canceled.status?.state !== TaskState.TASK_STATE_CANCELED) throw new Error(`state ${canceled.status?.state}`);
    return "TASK_STATE_CANCELED";
  });

  await check(protocol, "unknown task is an error", async () => {
    try {
      await client.getTask({ tenant: "", id: `missing-${randomUUID()}`, historyLength: undefined }, opts);
    } catch (error) {
      return `rejected (${String(error.message ?? error).slice(0, 60)})`;
    }
    throw new Error("no error for unknown task");
  });
}

const failed = results.filter((r) => !r.ok).length;
console.log(`\n${results.length - failed}/${results.length} passed`);
process.exit(failed ? 1 : 0);
