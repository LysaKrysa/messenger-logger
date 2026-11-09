package main

import (
	"cmp"
	"context"
	"encoding/json"
	"fmt"
	"strconv"
	"sync"
	"time"

	"fbchat-bridge-e2ee/bridge"
	"github.com/rs/zerolog"
	"go.mau.fi/mautrix-meta/pkg/instameow"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
	"go.mau.fi/mautrix-meta/pkg/messagix/cookies"
	"go.mau.fi/mautrix-meta/pkg/messagix/table"
	"go.mau.fi/mautrix-meta/pkg/messagix/types"
)

var instagram *instagramClient

type instagramClient struct {
	client  *instameow.Client
	account string
	sender  int64
	cancel  context.CancelFunc
	threads map[string]string

	mu    sync.Mutex
	state string
}

func newInstagram(cfg *bridge.ClientConfig) (*instagramClient, error) {
	session := &cookies.Cookies{Platform: types.Instagram}
	values := make(map[cookies.MetaCookieName]string, len(cfg.Cookies))
	for key, value := range cfg.Cookies {
		values[cookies.MetaCookieName(key)] = value
	}
	session.UpdateValues(values)
	account := session.Get(cookies.IGCookieDSUserID)
	id, err := strconv.ParseUint(account, 10, 64)
	if err != nil || id == 0 || !session.IsLoggedIn() {
		return nil, fmt.Errorf("Instagram session cookies are missing or invalid")
	}
	ig := &instagramClient{account: account, threads: make(map[string]string)}
	ig.client = instameow.NewClient(instameow.ClientParams{
		Cookies:       session,
		Log:           zerolog.Nop(),
		EventHandler:  ig.event,
		DisableTyping: true,
	})
	return ig, nil
}

func (ig *instagramClient) setState(state string) {
	ig.mu.Lock()
	defer ig.mu.Unlock()
	ig.state = state
}

func (ig *instagramClient) status() map[string]any {
	ig.mu.Lock()
	defer ig.mu.Unlock()
	result := map[string]any{
		"connected": ig.state == "ready", "e2eeConnected": false,
		"nativeReconnecting": ig.state == "waiting",
	}
	switch ig.state {
	case "waiting", "stopped":
		result["failureCode"], result["retryable"], result["permanent"] = "network_disconnected", true, false
	case "auth_failed", "checkpoint_required", "protocol_error", "config_invalid":
		result["failureCode"], result["retryable"], result["permanent"] = ig.state, false, true
	}
	return result
}

func instagramFailure(id uint64, err error) {
	classification := bridge.ClassifyConnectionError(err)
	code, retryable := "connection_failed", true
	if classification.Classified {
		code, retryable = string(classification.Code), classification.Retryable
	}
	writeJSON(response{ID: id, OK: false, Error: "Instagram connection failed", ErrorCode: code, Retryable: &retryable})
}

func (ig *instagramClient) connect(id uint64) {
	if ig.cancel != nil {
		fail(id, fmt.Errorf("Instagram connection already started"))
		return
	}
	startup, stopStartup := context.WithTimeout(context.Background(), 100*time.Second)
	defer stopStartup()
	viewer, mailbox, err := ig.client.LoadIndex(startup)
	if err != nil {
		instagramFailure(id, err)
		return
	}
	sender := ig.client.GetOwnFBID()
	if viewer == nil || viewer.ID != ig.account || sender <= 0 {
		retryable := false
		writeJSON(response{ID: id, OK: false, Error: "Instagram account identity did not match",
			ErrorCode: "auth_failed", Retryable: &retryable})
		return
	}
	ig.sender = sender
	// The inbox supplies the current sequence and names. Do not emit any of
	// the messages included in that initial response.
	for _, node := range mailbox.ThreadsByFolder.Edges {
		thread := node.Node.AsIGDirectThread
		if thread != nil && thread.ThreadTitle != "" {
			ig.threads[cmp.Or(thread.ThreadFBID, thread.ID)] = thread.ThreadTitle
		}
	}
	ctx, cancel := context.WithCancel(context.Background())
	ig.cancel = cancel
	ig.setState("waiting")
	go func() {
		ig.client.Connect(ctx)
		ig.mu.Lock()
		defer ig.mu.Unlock()
		if ig.state == "waiting" || ig.state == "ready" {
			ig.state = "stopped"
		}
	}()
	ok(id, map[string]any{"user": map[string]any{
		"id": viewer.ID, "senderId": strconv.FormatInt(sender, 10),
		"name": cmp.Or(viewer.GetName(), viewer.GetUsername()),
	}})
}

func disconnectInstagram() {
	if instagram != nil && instagram.cancel != nil {
		instagram.cancel()
		instagram.client.Disconnect()
	}
	instagram = nil
}

func (ig *instagramClient) handle(req *request) {
	switch req.Method {
	case "connect":
		ig.connect(req.ID)
	case "isConnected":
		ok(req.ID, ig.status())
	case "downloadMedia":
		var p struct {
			URL string `json:"url"`
		}
		if err := json.Unmarshal(req.Params, &p); err != nil {
			fail(req.ID, fmt.Errorf("invalid Instagram media request"))
			return
		}
		data, err := bridge.DownloadMediaURL(p.URL)
		if err != nil {
			fail(req.ID, fmt.Errorf("Instagram media unavailable"))
			return
		}
		ok(req.ID, map[string]any{"data": data})
	case "disconnect":
		disconnectInstagram()
		ok(req.ID, map[string]any{})
	default:
		fail(req.ID, fmt.Errorf("method unavailable for Instagram"))
	}
}

func (ig *instagramClient) event(_ context.Context, event slidetypes.ClientEvent) error {
	switch e := event.(type) {
	case *slidetypes.Connected:
		ig.setState("ready")
	case *slidetypes.Disconnected:
		classification := bridge.ClassifyConnectionError(e.Error)
		if classification.Permanent {
			ig.setState(string(classification.Code))
			return fmt.Errorf("Instagram session requires attention")
		}
		ig.setState("waiting")
	case *slidetypes.AuthError:
		ig.setState("auth_failed")
		if ig.cancel != nil {
			ig.cancel()
		}
	case *slidetypes.Delta:
		if captured := ig.delta(e); captured != nil {
			writeJSON(eventEnvelope{Event: captured})
		}
	}
	return nil
}

func (ig *instagramClient) delta(delta *slidetypes.Delta) *bridge.Event {
	switch e := delta.Data.(type) {
	case *slidetypes.NewMessageEvent:
		msg := e.Message
		if msg == nil || msg.MessageID == "" || msg.SenderFBID <= 0 || msg.SenderFBID == ig.sender ||
			msg.TimestampMS.Time.IsZero() || (msg.Sender != nil && msg.Sender.IGID == ig.account) {
			return nil
		}
		thread := cmp.Or(delta.ThreadIGID, msg.ThreadFBID)
		if thread == "" {
			return nil
		}
		body, media := instagramContent(msg)
		if body == "" && len(media) == 0 {
			return nil
		}
		stamp := msg.TimestampMS.Time.UnixMilli()
		data := map[string]any{
			"id": msg.MessageID, "threadId": thread, "senderId": msg.SenderFBID,
			"text": body, "timestampMs": stamp, "attachments": media,
		}
		if msg.Sender != nil {
			data["senderName"] = cmp.Or(msg.Sender.UserDict.FullName, msg.Sender.Name)
			data["senderUsername"] = msg.Sender.UserDict.Username
		}
		if title := ig.threads[thread]; title != "" {
			data["threadName"] = title
		}
		if msg.RepliedToMessageID != "" {
			data["replyTo"] = bridge.ReplyTo{MessageID: msg.RepliedToMessageID}
		}
		return &bridge.Event{Type: bridge.EventTypeMessage, Timestamp: stamp, Data: data}
	case *slidetypes.DeleteMessageEvent:
		if e.MessageID != "" {
			return &bridge.Event{Type: bridge.EventTypeMessageUnsend, Timestamp: time.Now().UnixMilli(),
				Data: map[string]any{"messageId": e.MessageID, "threadId": delta.ThreadIGID}}
		}
	}
	return nil
}

func instagramContent(msg *slidetypes.Message) (string, []bridge.Attachment) {
	var media []bridge.Attachment
	body := msg.TextBody
	switch content := msg.Content.Content.(type) {
	case *slidetypes.MessageContentText:
		body = cmp.Or(content.TextBody, body)
	case *slidetypes.MessageContentImage:
		for _, item := range content.Attachments {
			media = append(media, instagramAttachment(item, "image", ""))
		}
	case *slidetypes.MessageContentVideo:
		for _, item := range content.Videos {
			media = append(media, instagramAttachment(item, "video", ""))
		}
	case *slidetypes.MessageContentMultiMedia:
		for _, item := range content.Attachments {
			kind := "image"
			if item != nil && table.AttachmentType(item.AttachmentType) == table.AttachmentTypeVideo {
				kind = "video"
			}
			media = append(media, instagramAttachment(item, kind, ""))
		}
	case *slidetypes.MessageContentAudio:
		for _, item := range content.AudioAttachments {
			if item != nil {
				media = append(media, bridge.Attachment{Type: "voice", URL: item.AttachmentCDNURL,
					MimeType: "audio/mp4", Duration: item.PlayableDurationMS / 1000})
			}
		}
	case *slidetypes.MessageContentRavenImage:
		media = append(media, instagramAttachment(content.Attachment, "image", instagramViewMode(content.ViewMode)))
	case *slidetypes.MessageContentRavenVideo:
		media = append(media, instagramAttachment(content.Attachment, "video", instagramViewMode(content.ViewMode)))
	}
	return body, media
}

func instagramViewMode(mode slidetypes.RavenViewMode) string {
	switch mode {
	case slidetypes.RavenViewModeViewOnce:
		return "once"
	case slidetypes.RavenViewModeViewTwice:
		return "replay"
	case slidetypes.RavenViewModeKeepInChat:
		return "keep"
	default:
		return "limited"
	}
}

func instagramAttachment(item *slidetypes.Attachment, kind, mode string) bridge.Attachment {
	att := bridge.Attachment{Type: kind, ViewMode: mode, ViewOnce: mode != "" && mode != "keep"}
	if item != nil {
		att.URL = cmp.Or(item.AttachmentCDNURL, item.AttachmentCDNFallbackURL)
		att.Width, att.Height = item.PreviewWidth, item.PreviewHeight
		if kind == "image" {
			att.URL = cmp.Or(att.URL, item.PreviewCDNURL, item.PreviewCDNFallbackURL)
		}
	}
	return att
}
