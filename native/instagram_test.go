package main

import (
	"encoding/json"
	"fmt"
	"testing"

	"fbchat-bridge-e2ee/bridge"
	"go.mau.fi/mautrix-meta/pkg/instameow/slidetypes"
)

func parseInstagramDelta(t *testing.T, raw string) *slidetypes.Delta {
	t.Helper()
	var delta slidetypes.Delta
	if err := json.Unmarshal([]byte(raw), &delta); err != nil {
		t.Fatal(err)
	}
	return &delta
}

func TestInstagramLiveMessageAndUnsend(t *testing.T) {
	ig := &instagramClient{account: "55", sender: 412}
	const raw = `{"__typename":"SlideUQPPNewMessage","message":{
		"message_id":"mid.example","thread_fbid":"340282366841710300949128777",
		"sender_fbid":"%s","timestamp_ms":"1794190000000",
		"content":{"__typename":"SlideMessageText","text_body":"fresh"},
		"sender":{"name":"Jordan","user_dict":{"username":"jordan"}}}}`
	evt := ig.delta(parseInstagramDelta(t, fmt.Sprintf(raw, "413")))
	if evt == nil || evt.Type != bridge.EventTypeMessage {
		t.Fatalf("missing message: %#v", evt)
	}
	data := evt.Data.(map[string]any)
	if data["threadId"] != "340282366841710300949128777" || data["text"] != "fresh" ||
		data["senderUsername"] != "jordan" || data["timestampMs"] != int64(1794190000000) {
		t.Fatalf("incorrect live message: %#v", data)
	}
	if ig.delta(parseInstagramDelta(t, fmt.Sprintf(raw, "412"))) != nil {
		t.Fatal("emitted the signed-in account's outgoing message")
	}
	unsend := ig.delta(parseInstagramDelta(t, `{"__typename":"SlideUQPPDeleteMessage","message_id":"mid.example"}`))
	if unsend == nil || unsend.Type != bridge.EventTypeMessageUnsend ||
		unsend.Data.(map[string]any)["messageId"] != data["id"] {
		t.Fatalf("did not correlate unsend: %#v", unsend)
	}
	if ig.delta(parseInstagramDelta(t, `{"__typename":"SlideUQPPReadReceipt"}`)) != nil {
		t.Fatal("emitted a read receipt")
	}
}

func TestInstagramRavenMedia(t *testing.T) {
	ig := &instagramClient{account: "55", sender: 412}
	const raw = `{"__typename":"SlideUQPPNewRavenMessage","thread_fbid":"234","message":{
		"message_id":"mid.raven","sender_fbid":"413","timestamp_ms":"1794190000000","content":%s}}`
	for _, tc := range []struct {
		name, content, kind, mode, url string
		viewOnce                       bool
	}{
		{"photo", `{"__typename":"SlideMessageRavenImageContent","view_mode":0,"attachment":{"attachment_cdn_url":"https://scontent.cdninstagram.com/photo"}}`,
			"image", "once", "https://scontent.cdninstagram.com/photo", true},
		{"video", `{"__typename":"SlideMessageRavenVideoContent","view_mode":1,"attachment":{"attachment_cdn_url":"https://scontent.cdninstagram.com/video"}}`,
			"video", "replay", "https://scontent.cdninstagram.com/video", true},
		{"expired", `{"__typename":"SlideMessageRavenImageContent","view_mode":0,"attachment":null}`,
			"image", "once", "", true},
		{"video with only preview", `{"__typename":"SlideMessageRavenVideoContent","view_mode":0,"attachment":{"preview_cdn_url":"https://scontent.cdninstagram.com/thumbnail"}}`,
			"video", "once", "", true},
		{"kept photo", `{"__typename":"SlideMessageRavenImageContent","view_mode":2,"attachment":{"preview_cdn_url":"https://scontent.cdninstagram.com/photo"}}`,
			"image", "keep", "https://scontent.cdninstagram.com/photo", false},
	} {
		t.Run(tc.name, func(t *testing.T) {
			evt := ig.delta(parseInstagramDelta(t, fmt.Sprintf(raw, tc.content)))
			if evt == nil {
				t.Fatal("missing media event")
			}
			media := evt.Data.(map[string]any)["attachments"].([]bridge.Attachment)
			if len(media) != 1 || media[0].Type != tc.kind || media[0].ViewMode != tc.mode ||
				media[0].ViewOnce != tc.viewOnce || media[0].URL != tc.url {
				t.Fatalf("incorrect media event: %#v", media)
			}
		})
	}
}
