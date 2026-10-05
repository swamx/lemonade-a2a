// Independent-client interoperability check using the official A2A Go SDK
// (github.com/a2aproject/a2a-go/v2), as a library rather than through a CLI.
//
//	cd interop/go && go run . [-url http://127.0.0.1:9100] [-auth <key>]
//
// Runs the same scenarios as ../js_client.mjs over JSON-RPC and HTTP+JSON and
// exits non-zero if any fails. The backend should be slow enough (a few seconds)
// that the cancel scenario catches the task while it is running.
package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/a2aproject/a2a-go/v2/a2a"
	"github.com/a2aproject/a2a-go/v2/a2aclient"
	"github.com/a2aproject/a2a-go/v2/a2aclient/agentcard"
)

// bearer injects an Authorization header on every call.
type bearer struct {
	a2aclient.PassthroughInterceptor
	key string
}

func (b bearer) Before(ctx context.Context, req *a2aclient.Request) (context.Context, any, error) {
	if b.key != "" {
		req.ServiceParams["Authorization"] = []string{"Bearer " + b.key}
	}
	return ctx, nil, nil
}

type result struct {
	ok     bool
	name   string
	detail string
}

func artifactText(task *a2a.Task) string {
	var sb strings.Builder
	for _, art := range task.Artifacts {
		for _, part := range art.Parts {
			sb.WriteString(part.Text())
		}
	}
	return sb.String()
}

func user(text string) *a2a.SendMessageRequest {
	return &a2a.SendMessageRequest{Message: a2a.NewMessage(a2a.MessageRoleUser, a2a.NewTextPart(text))}
}

func newClient(ctx context.Context, url, key string, protocol a2a.TransportProtocol) (*a2aclient.Client, error) {
	card, err := agentcard.DefaultResolver.Resolve(ctx, url)
	if err != nil {
		return nil, fmt.Errorf("resolve card: %w", err)
	}
	return a2aclient.NewFromCard(ctx, card,
		a2aclient.WithConfig(a2aclient.Config{PreferredTransports: []a2a.TransportProtocol{protocol}}),
		a2aclient.WithCallInterceptors(bearer{key: key}),
	)
}

func main() {
	url := flag.String("url", "http://127.0.0.1:9100", "adapter base URL")
	key := flag.String("auth", "", "API key if the adapter enforces one")
	flag.Parse()

	var results []result
	record := func(protocol a2a.TransportProtocol, name string, err error, detail string) {
		r := result{ok: err == nil, name: fmt.Sprintf("%s %s", protocol, name), detail: detail}
		if err != nil {
			r.detail = err.Error()
		}
		results = append(results, r)
		status := "PASS"
		if !r.ok {
			status = "FAIL"
		}
		fmt.Printf("%s %s - %s\n", status, r.name, r.detail)
	}

	for _, protocol := range []a2a.TransportProtocol{a2a.TransportProtocolJSONRPC, a2a.TransportProtocolHTTPJSON} {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
		client, err := newClient(ctx, *url, *key, protocol)
		if err != nil {
			record(protocol, "connect", err, "")
			cancel()
			continue
		}

		// send message
		resp, err := client.SendMessage(ctx, user("hello"))
		if err == nil {
			task, isTask := resp.(*a2a.Task)
			switch {
			case !isTask:
				err = fmt.Errorf("expected a task, got %T", resp)
			case task.Status.State != a2a.TaskStateCompleted:
				err = fmt.Errorf("state %s", task.Status.State)
			case strings.TrimSpace(artifactText(task)) == "":
				err = fmt.Errorf("no artifact text")
			default:
				record(protocol, "send message", nil, fmt.Sprintf("completed, %d chars", len(artifactText(task))))
			}
		}
		if err != nil {
			record(protocol, "send message", err, "")
		}

		// streaming
		chunks, final := 0, a2a.TaskState("")
		var streamErr error
		for event, e := range client.SendStreamingMessage(ctx, user("hello")) {
			if e != nil {
				streamErr = e
				break
			}
			switch ev := event.(type) {
			case *a2a.TaskArtifactUpdateEvent:
				chunks++
			case *a2a.TaskStatusUpdateEvent:
				final = ev.Status.State
			}
		}
		switch {
		case streamErr != nil:
			record(protocol, "streaming", streamErr, "")
		case chunks < 2:
			record(protocol, "streaming", fmt.Errorf("only %d artifact chunks", chunks), "")
		case final != a2a.TaskStateCompleted:
			record(protocol, "streaming", fmt.Errorf("final state %q", final), "")
		default:
			record(protocol, "streaming", nil, fmt.Sprintf("%d artifact chunks, completed", chunks))
		}

		// get / list
		sent, err := client.SendMessage(ctx, user("hello"))
		if err == nil {
			id := sent.(*a2a.Task).ID
			var got *a2a.Task
			if got, err = client.GetTask(ctx, &a2a.GetTaskRequest{ID: id}); err == nil && got.ID != id {
				err = fmt.Errorf("getTask returned another task")
			}
			if err == nil {
				var listed *a2a.ListTasksResponse
				if listed, err = client.ListTasks(ctx, &a2a.ListTasksRequest{}); err == nil {
					found := false
					for _, t := range listed.Tasks {
						found = found || t.ID == id
					}
					if !found {
						err = fmt.Errorf("task missing from ListTasks")
					}
				}
			}
		}
		record(protocol, "get / list task", err, "found in list")

		// cancel a running task
		var taskID a2a.TaskID
		for event, e := range client.SendStreamingMessage(ctx, user("long running")) {
			if e != nil {
				err = e
				break
			}
			if t, ok := event.(*a2a.Task); ok {
				taskID = t.ID
			}
			if _, ok := event.(*a2a.TaskArtifactUpdateEvent); ok {
				break
			}
		}
		if err == nil {
			var canceled *a2a.Task
			if canceled, err = client.CancelTask(ctx, &a2a.CancelTaskRequest{ID: taskID}); err == nil &&
				canceled.Status.State != a2a.TaskStateCanceled {
				err = fmt.Errorf("state %s", canceled.Status.State)
			}
		}
		record(protocol, "cancel running task", err, "TASK_STATE_CANCELED")

		// unknown task is an error
		_, err = client.GetTask(ctx, &a2a.GetTaskRequest{ID: a2a.NewTaskID()})
		if err == nil {
			record(protocol, "unknown task is an error", fmt.Errorf("no error for unknown task"), "")
		} else {
			record(protocol, "unknown task is an error", nil, "rejected ("+err.Error()+")")
		}
		cancel()
	}

	failed := 0
	for _, r := range results {
		if !r.ok {
			failed++
		}
	}
	fmt.Printf("\n%d/%d passed\n", len(results)-failed, len(results))
	if failed > 0 {
		os.Exit(1)
	}
}
