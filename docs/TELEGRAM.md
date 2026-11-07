# Telegram setup and topic repair

[← README](../README.md#telegram-forwarding) · [Troubleshooting](TROUBLESHOOTING.md)


## Download buttons in the shared bot

New document notifications sent through the dashboard gateway include an inline
**Download file** button. Voice recordings are sent as documents too. Bundled
notifications use **Download ZIP**. The original notification remains one file
plus its caption; the gateway does not send another message automatically.

Pressing the button explicitly requests one downloadable document copy, replying
to the original message in the same private conversation topic. Open that copy
and use Telegram's Save/Download action. A bot cannot force a download into your
phone's storage. The requested copy has no button of its own.

The shared receiver verifies the private chat, pressing user's identity, approved
pairing, original bot author and document metadata from Telegram. The callback
contains no file ID or URL. Workers cannot inject buttons, choose another file,
call callback/file APIs or expose the bot token through a download link.

Copies reuse Telegram's existing document ID; no public file URL, local archive
read or additional media upload is needed. A paired owner can request a copy
while capture is paused and after local 72-hour retention expires. Disconnecting
or removing the account disables its button requests; existing Telegram files
retain their native Save/Download controls.

Duplicate updates are committed before replies and never automatically replayed.
Rapid taps are limited. An uncertain send is not automatically retried. The
single invitation receiver also handles callbacks; do not run another poller.

Only new shared-gateway uploads get these buttons. Existing messages and the
standalone relay keep Telegram's built-in attachment download controls. To apply
this feature, replace the release's dashboard/broker.py and restart
messenger-dashboard-bot; account workers need no rebuild or restart.

Reference: [Telegram inline keyboards and callbacks](https://core.telegram.org/bots/api#inlinekeyboardbutton).


## Initial setup

1. Create/select a bot with **@BotFather**.
2. Open its chat and send `/start`.
3. Get the intended destination's numeric chat ID.
4. Edit the private `messenger.env` with the token, chat ID, and forwarding options.
5. Restart the service to reload environment changes.

For a private bot chat, enable **Threaded mode** for that same bot in BotFather.
For a supergroup, enable **Topics** and give the bot **Manage Topics** admin rights.
The logger also needs `TELEGRAM_TOPICS=true`.

The logger does not discover bot updates or print tokens on your behalf. Keep
token-bearing URLs and update payloads private; they can contain account data.

## Read-only verification

```bash
sudo ./check-telegram-topics
```

The check uses only `getMe`, `getChat`, and, for a forum group, `getChatMember`.
It verifies the configured bot ID, reports its public username, checks
`getMe.has_topics_enabled` for private topic support, and checks the destination
type/group permission.

| Output | Action |
| :--- | :--- |
| Logger forwarding disabled | Set `TELEGRAM_ENABLED=true`, then restart. |
| Logger conversation topics disabled | Set `TELEGRAM_TOPICS=true`, then restart. |
| Telegram bot Threaded mode disabled/not reported | Verify the displayed bot in BotFather and its Threaded-mode setting. |
| Destination is not private or forum supergroup | Correct the chat ID or enable group Topics. |
| Missing Manage Topics | Give the configured bot that administrator permission. |
| HTTP 401 | Check/refresh the configured bot token privately. |
| Ready | Restart the listener and trigger a fresh eligible forwarded event. |

A failed setup check has a 30-second cooldown. A restarted process checks again
immediately. The standalone diagnostic always performs a fresh check.

Ready confirms setup and capability, not successful delivery or the existence
of every saved topic. A send can still fail after the check passes.

With `TELEGRAM_MODE=deleted`, ask another person to send you a new Messenger
message and unsend it after capture. Your own messages are excluded. An ordinary
incoming message alone is not a deletion-forwarding test.
Setting changes do not replay earlier failed notices.

## Delivery behavior

Only incoming content is captured and forwarded. Messages sent by the logged-in
account, including quoted copies of their text, are excluded. Deletions of unknown
messages wait briefly for an incoming original instead of producing an unattributed
notice.

Each deletion is sent in **one Telegram message**, after pending downloads finish.
The notice includes available names, conversation titles, timestamps, original/latest
captured text, and attachment descriptions. One saved attachment is a document with
the details in its caption. Multiple files or a long caption become one ZIP with
the original files and a full `message.txt` transcript. Long text without media is
one downloadable text file. This preserves full text without splitting a notice.
Voice recordings and other documents have Telegram's native download/save controls.
No public download link exposes the bot token or private media URLs.

Failed downloads are described in the same notice. A combined ZIP or individual
file over the Cloud Bot API's 50,000,000-byte limit stays local, with one explanatory
notice. Local attachment download limits are separate from Telegram's upload limit.
In `all` mode an incoming message and its later deletion are separate notifications;
each notification still contains its own details and files together.

Sends are claimed in a local ledger before the request. Ambiguous sends are not
automatically retried, avoiding duplicates. Queue overflow and failed downloads
do not stop normal local capture.

Failures include a safe method, status, and fixed reason category, for example:

```text
Telegram relay failed (method=sendMessage; status=400; reason=topic_missing); captured content remains local. No automatic resend.
```

The logger reads only a bounded error response and never prints the provider's
description, token-bearing URL, message text, or request body. Categories are
best-effort interpretations of Telegram errors; an unfamiliar rejection stays
`request_rejected`.

| Reason | Next step |
| :--- | :--- |
| `topic_missing` | A missing saved topic is repaired automatically once. If Telegram also rejects the replacement, bind a verified existing topic as described below. |
| `topic_closed` | Reopen that topic in Telegram, then test a fresh event. |
| `bot_blocked`, `bot_not_started` | Unblock the configured bot and open its chat with `/start`. |
| `unauthorized`, `chat_not_found`, `permission_denied` | Check the bot token, destination, and permissions privately. |
| `invalid_entities`, `message_too_long`, `empty_text` | Report the safe diagnostic line and whether the failure affected text or an attachment. Keep message contents private. |
| `rate_limited` | Allow Telegram's rate limit to clear before testing a fresh event. |
| `dns_error`, `tls_error`, `timeout`, `connection_error`, `server_error` | Check server connectivity and Telegram availability. Delivery may be unconfirmed. |
| `request_rejected`, `invalid_response`, `invalid_response_json`, `local_or_unclassified_error` | Share the safe diagnostic line to narrow down the cause. |

Test with a **new** eligible event after correcting the cause. Restarting does
not replay failed sends. Do not clear the attempt ledger to force a resend:
an earlier ambiguous request may already have reached Telegram.

## Missing topic recovery

Telegram can reject a previously saved topic ID with HTTP 400 and a specific
topic-not-found error. The logger replaces only that conversation's mapping,
creates a replacement topic, and retries only the rejected request once. This
applies to a text page or one media upload; already accepted pages and earlier
messages are not replayed. Existing Telegram topics and messages are not deleted.

The replacement is confirmed after a successful send. If its creation times
out, the map stays `unconfirmed` and requires manual binding. If sending to the
replacement times out, its ID stays `repair_pending`; the uncertain message is
not resent, and a future eligible event can use that same ID. If Telegram also
reports the replacement missing, its state becomes `missing` and further
automatic creation is paused, including after a restart. Use the local bind
command below to select a verified existing topic.

Rate limits, formatting errors, blocked bots, and ambiguous network failures
do not trigger topic replacement. Previously failed notification records are
not replayed when installing this update; test with a fresh eligible event.

Topic mappings are scoped by bot ID, destination chat ID, and Messenger
conversation ID. Changing the destination creates a new map. Same-name
conversations get distinct topic titles. Topic setup failure does not fall back
to mixing content into the destination's main chat.

## Unconfirmed topic creation

A network timeout can occur after Telegram creates a topic but before the
response reaches the logger. The map records this as unconfirmed and does not
blindly create another topic.

List local mappings, using only the numeric bot ID (the token prefix before
the colon), never the full token:

```bash
sudo ./manage-topics list --bot-id BOT_ID --chat-id CHAT_ID
```

Use `show-messages --include-ids` locally for the Messenger conversation ID.
Obtain the existing Telegram topic's numeric `message_thread_id` from your own
Telegram client/API context, then bind that existing topic:

```bash
sudo ./manage-topics bind \
  --bot-id BOT_ID --chat-id CHAT_ID \
  --conversation MESSENGER_CONVERSATION_ID \
  --topic-id EXISTING_TOPIC_ID --name "Conversation title"
```

For a negative group ID, use `--chat-id=-1001234567890` if your shell/argument
parser interprets a separate negative value as an option.

The repair utility writes only the local mapping; it makes no Telegram request.
It does not verify that a manually supplied topic ID belongs to the intended
destination, so check those IDs before binding.

Telegram copies are intentionally outside local retention. The logger does
not delete Telegram messages, files, chats, or topics during cleanup.

## Official references

- [Bot forums and Threaded mode](https://core.telegram.org/api/forum)
- [Bot API User capability fields](https://core.telegram.org/bots/api#user)
- [createForumTopic](https://core.telegram.org/bots/api#createforumtopic)
