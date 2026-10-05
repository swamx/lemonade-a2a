package interop;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.function.BiConsumer;

import org.a2aproject.sdk.A2A;
import org.a2aproject.sdk.client.Client;
import org.a2aproject.sdk.client.ClientEvent;
import org.a2aproject.sdk.client.MessageEvent;
import org.a2aproject.sdk.client.TaskEvent;
import org.a2aproject.sdk.client.TaskUpdateEvent;
import org.a2aproject.sdk.client.config.ClientConfig;
import org.a2aproject.sdk.client.http.A2ACardResolver;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransport;
import org.a2aproject.sdk.client.transport.jsonrpc.JSONRPCTransportConfig;
import org.a2aproject.sdk.client.transport.rest.RestTransport;
import org.a2aproject.sdk.client.transport.rest.RestTransportConfig;
import org.a2aproject.sdk.client.transport.spi.interceptors.ClientCallContext;
import org.a2aproject.sdk.spec.AgentCard;
import org.a2aproject.sdk.spec.Artifact;
import org.a2aproject.sdk.spec.CancelTaskParams;
import org.a2aproject.sdk.spec.ListTasksParams;
import org.a2aproject.sdk.spec.Part;
import org.a2aproject.sdk.spec.Task;
import org.a2aproject.sdk.spec.TaskArtifactUpdateEvent;
import org.a2aproject.sdk.spec.TaskQueryParams;
import org.a2aproject.sdk.spec.TextPart;

/**
 * Independent-client interoperability check using the official A2A Java SDK
 * (org.a2aproject.sdk 1.4.0.Final).
 *
 * <pre>
 *   cd interop/java && mvn -q package dependency:copy-dependencies
 *   java -cp "target/classes:target/lib/*" interop.Main [baseUrl] [--auth KEY]
 * </pre>
 *
 * Runs the same scenarios as ../js_client.mjs over JSON-RPC and HTTP+JSON; the exit
 * code is non-zero if any scenario fails.
 */
public final class Main {

    /** Everything the SDK's event callbacks deliver for one request. */
    static final class Events {
        final List<ClientEvent> all = new ArrayList<>();
        final CompletableFuture<Task> finalTask = new CompletableFuture<>();
        volatile String taskId;
        volatile int artifactChunks;

        synchronized void accept(ClientEvent event) {
            all.add(event);
            if (event instanceof TaskEvent e) {
                observe(e.getTask());
            } else if (event instanceof TaskUpdateEvent e) {
                observe(e.getTask());
                if (e.getUpdateEvent() instanceof TaskArtifactUpdateEvent) {
                    artifactChunks++;
                }
            }
        }

        private void observe(Task task) {
            taskId = task.id();
            if (task.status().state().isFinal()) {
                finalTask.complete(task);
            }
        }
    }

    static String artifactText(Task task) {
        StringBuilder text = new StringBuilder();
        if (task.artifacts() != null) {
            for (Artifact artifact : task.artifacts()) {
                for (Part<?> part : artifact.parts()) {
                    if (part instanceof TextPart tp) {
                        text.append(tp.text());
                    }
                }
            }
        }
        return text.toString();
    }

    static Client client(AgentCard card, String protocol, boolean streaming, Events events) throws Exception {
        List<BiConsumer<ClientEvent, AgentCard>> consumers = List.of((event, c) -> events.accept(event));
        var builder = Client.builder(card)
                .clientConfig(ClientConfig.builder().setStreaming(streaming).build())
                .addConsumers(consumers)
                .streamingErrorHandler(events.finalTask::completeExceptionally);
        if (protocol.equals("JSONRPC")) {
            builder.withTransport(JSONRPCTransport.class, new JSONRPCTransportConfig());
        } else {
            builder.withTransport(RestTransport.class, new RestTransportConfig());
        }
        return builder.build();
    }

    interface Scenario {
        String run() throws Exception;
    }

    static int passed = 0;
    static int failed = 0;

    static void check(String protocol, String name, Scenario scenario) {
        try {
            System.out.printf("PASS %s %s - %s%n", protocol, name, scenario.run());
            passed++;
        } catch (Throwable error) {
            System.out.printf("FAIL %s %s - %s: %s%n", protocol, name, error.getClass().getSimpleName(), error.getMessage());
            for (StackTraceElement frame : java.util.Arrays.copyOf(error.getStackTrace(), Math.min(6, error.getStackTrace().length))) {
                System.out.println("       at " + frame);
            }
            failed++;
        }
    }

    public static void main(String[] args) throws Exception {
        String baseUrl = "http://127.0.0.1:9100";
        String auth = null;
        for (int i = 0; i < args.length; i++) {
            if (args[i].equals("--auth")) {
                auth = args[++i];
            } else {
                baseUrl = args[i];
            }
        }
        Map<String, String> headers = auth == null ? Map.of() : Map.of("Authorization", "Bearer " + auth);
        ClientCallContext context = new ClientCallContext(Map.of(), headers);
        AgentCard card = A2ACardResolver.builder().baseUrl(baseUrl).build().getAgentCard();

        for (String protocol : List.of("JSONRPC", "HTTP+JSON")) {
            check(protocol, "send message", () -> {
                Events events = new Events();
                try (Client client = client(card, protocol, false, events)) {
                    client.sendMessage(A2A.toUserMessage("hello"), context);
                    Task task = events.finalTask.get(120, TimeUnit.SECONDS);
                    if (!task.status().state().name().contains("COMPLETED")) {
                        throw new IllegalStateException("state " + task.status().state());
                    }
                    String text = artifactText(task);
                    if (text.isBlank()) {
                        throw new IllegalStateException("no artifact text");
                    }
                    return "completed, " + text.length() + " chars";
                }
            });

            check(protocol, "streaming", () -> {
                Events events = new Events();
                try (Client client = client(card, protocol, true, events)) {
                    client.sendMessage(A2A.toUserMessage("hello"), context);
                    Task task = events.finalTask.get(120, TimeUnit.SECONDS);
                    if (events.artifactChunks < 2) {
                        throw new IllegalStateException("only " + events.artifactChunks + " artifact chunks");
                    }
                    if (!task.status().state().name().contains("COMPLETED")) {
                        throw new IllegalStateException("final state " + task.status().state());
                    }
                    return events.artifactChunks + " artifact chunks, completed";
                }
            });

            check(protocol, "get / list task", () -> {
                Events events = new Events();
                try (Client client = client(card, protocol, false, events)) {
                    client.sendMessage(A2A.toUserMessage("hello"), context);
                    Task sent = events.finalTask.get(120, TimeUnit.SECONDS);
                    Task fetched = client.getTask(new TaskQueryParams(sent.id()), context);
                    if (!fetched.id().equals(sent.id())) {
                        throw new IllegalStateException("getTask returned another task");
                    }
                    // An explicit empty tenant: the SDK's JSON-RPC mapper rejects null (NPE in setTenant).
                    // pageSize == 1 with a context filter: SDK 1.4.0 rejects any response whose pageSize differs from the
                    // number of tasks returned, although the A2A spec's own example returns pageSize 10 for 1 task.
                    var listed = client.listTasks(ListTasksParams.builder().tenant("").contextId(sent.contextId()).pageSize(1).build(), context);
                    if (listed.tasks().stream().noneMatch(t -> t.id().equals(sent.id()))) {
                        throw new IllegalStateException("task missing from listTasks");
                    }
                    return "found in list";
                }
            });

            check(protocol, "cancel running task", () -> {
                Events events = new Events();
                try (Client client = client(card, protocol, true, events)) {
                    client.sendMessage(A2A.toUserMessage("long running"), context);
                    long deadline = System.currentTimeMillis() + 60_000;
                    while (events.artifactChunks < 1 && System.currentTimeMillis() < deadline) {
                        Thread.sleep(20);
                    }
                    Task canceled = client.cancelTask(new CancelTaskParams(events.taskId), context);
                    if (!canceled.status().state().name().contains("CANCEL")) {
                        throw new IllegalStateException("state " + canceled.status().state());
                    }
                    return "canceled (" + canceled.status().state() + ")";
                }
            });

            check(protocol, "unknown task is an error", () -> {
                Events events = new Events();
                try (Client client = client(card, protocol, false, events)) {
                    try {
                        client.getTask(new TaskQueryParams("missing-" + System.nanoTime()), context);
                    } catch (Exception expected) {
                        return "rejected (" + expected.getClass().getSimpleName() + ")";
                    }
                    throw new IllegalStateException("no error for unknown task");
                }
            });
        }

        System.out.printf("%n%d/%d passed%n", passed, passed + failed);
        System.exit(failed == 0 ? 0 : 1);
    }
}
