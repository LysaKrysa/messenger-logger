"""Build the logger's native bridge from exact upstream revisions plus local additions."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

UPSTREAM = "aa7abd0386ef1d4cabf8ce592d0f2cf308d8f4f6"
META = "cea2e38c9107be591bcd1b7a6562ccc72400ce23"
VERSION = "2.3.4-logger.2"


def run(*args, cwd=None, env=None):
    subprocess.run(args, cwd=cwd, env=env, check=True)


def checkout(url, revision, path):
    run("git", "init", "-q", str(path))
    run("git", "-C", str(path), "fetch", "-q", "--depth=1", url, revision)
    run("git", "-C", str(path), "checkout", "-q", "--detach", "FETCH_HEAD")
    actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
    if actual != revision:
        raise RuntimeError("Native source revision mismatch.")


def replace_once(path, old, new):
    source = path.read_text()
    if source.count(old) != 1:
        raise RuntimeError("Pinned native source hook changed: " + path.name)
    path.write_text(source.replace(old, new, 1))


def patch(source, additions):
    events, client, main = source / "bridge/events.go", source / "bridge/client.go", source / "main.go"
    replace_once(events, '\t// For E2EE media download\n', '\tViewOnce bool `json:"viewOnce,omitempty"`\n\tViewMode string `json:"viewMode,omitempty"`\n\t// For E2EE media download\n')
    # Add only to E2EEMessage, not to the other event structures using SenderID.
    replace_once(events, 'type E2EEMessage struct {\n', 'type E2EEMessage struct {\n\tSenderName string `json:"senderName,omitempty"`\n')
    replace_once(events, '\t\tChatJID:     e.Info.Chat.String(),\n', '\t\tChatJID:     e.Info.Chat.String(),\n\t\tSenderName: e.Info.PushName,\n')
    replace_once(events, '\t// Extract from ConsumerApplication\n', '\tif att, caption, handled := c.loggerSpecialMedia(e.Message); handled {\n\t\tmsg.Attachments = append(msg.Attachments, att)\n\t\tmsg.Text = caption\n\t\treturn msg\n\t}\n\n\t// Extract from ConsumerApplication\n')
    replace_once(events, '\t// Determine type based on AttachmentType (from table/enums.go)\n', '\tif blob.AttachmentType == table.AttachmentTypeEphemeralImage || blob.AttachmentType == table.AttachmentTypeEphemeralVideo {\n\t\tatt.ViewOnce, att.ViewMode = true, "limited"\n\t}\n\t// Determine type based on AttachmentType (from table/enums.go)\n')
    replace_once(events, 'func (c *Client) handleTable(tbl *table.LSTable) {\n', 'func (c *Client) handleTable(tbl *table.LSTable) {\n\tc.emitLoggerContacts(tbl)\n')
    replace_once(client, '\tif initialTable != nil {\n', '\tc.emitLoggerContacts(initialTable)\n\tif initialTable != nil {\n')
    replace_once(main, 'var bridgeVersion = "2.3.4"', 'var bridgeVersion = "' + VERSION + '"')
    replace_once(main, '\t"structuredConnectionStatus",\n', '\t"structuredConnectionStatus",\n\t"viewOnceMedia",\n\t"contactLookup",\n\t"instagramDMs",\n')
    replace_once(main, '\tcase "isConnected":\n', '\tcase "getUserInfo":\n\t\tif client == nil { fail(req.ID, fmt.Errorf("client not initialised")); return }\n\t\tvar p struct { UserID int64 `json:"userId"` }\n\t\tif err := json.Unmarshal(req.Params, &p); err != nil { fail(req.ID, err); return }\n\t\tcontact, err := client.LoggerContact(p.UserID)\n\t\tif err != nil { fail(req.ID, err); return }\n\t\tok(req.ID, contact)\n\n\tcase "isConnected":\n')
    for name in ("logger_media.go", "logger_media_test.go"):
        shutil.copyfile(additions / name, source / "bridge" / name)
    replace_once(main, 'func handle(req *request) {\n', '''func handle(req *request) {
	if instagram != nil && req.Method != "hello" {
		instagram.handle(req)
		return
	}
''')
    replace_once(main, '\t\tc, err := bridge.NewClient(&cfg)\n', '''		if cfg.Platform == "instagram" {
			ig, err := newInstagram(&cfg)
			if err != nil {
				fail(req.ID, err)
				return
			}
			instagram = ig
			ok(req.ID, map[string]any{"ready": true})
			return
		}
		c, err := bridge.NewClient(&cfg)
''')
    replace_once(main, '\tdefer disconnectClient()\n', '\tdefer disconnectClient()\n\tdefer disconnectInstagram()\n')
    media = source / "bridge/media.go"
    replace_once(media, '\t"fbcdn.net",\n', '\t"fbcdn.net",\n\t"cdninstagram.com",\n')
    replace_once(media, 'func (c *Client) DownloadMedia(rawURL string) ([]byte, error) {\n', '''func (c *Client) DownloadMedia(rawURL string) ([]byte, error) {
	return DownloadMediaURL(rawURL)
}

// DownloadMediaURL downloads from the allowlisted Meta CDNs without session cookies.
func DownloadMediaURL(rawURL string) ([]byte, error) {
''')
    for name in ("instagram.go", "instagram_test.go"):
        shutil.copyfile(additions / name, source / name)
    # Instameow adds two dependencies already locked in the pinned Meta module.
    dependencies = ("github.com/apache/thrift", "github.com/google/flatbuffers")
    requirements = [line for line in (source / "meta/go.mod").read_text().splitlines()
                    if line.lstrip().startswith(tuple(name + " " for name in dependencies))]
    sums = [line for line in (source / "meta/go.sum").read_text().splitlines()
            if line.startswith(tuple(name + " " for name in dependencies))]
    if len(requirements) != 2 or len(sums) != 4:
        raise RuntimeError("Pinned Instagram dependency locks changed.")
    anchor = "\tfilippo.io/edwards25519 v1.2.0 // indirect\n"
    replace_once(source / "go.mod", anchor, anchor + "".join(line + " // indirect\n" for line in requirements))
    with (source / "go.sum").open("a") as locks:
        locks.write("\n".join(sums) + "\n")


def build(source, output, *, arch=None, test=True):
    env = dict(os.environ, CGO_ENABLED="0", GOOS="linux", GOTOOLCHAIN="local")
    if arch:
        env["GOARCH"] = arch
    go = os.environ.get("LOGGER_GO", "go")
    if test:
        run(go, "test", "-mod=readonly", "./...", cwd=source, env=env)
    output.parent.mkdir(parents=True, exist_ok=True)
    run(go, "build", "-mod=readonly", "-trimpath", "-buildvcs=false", "-ldflags=-s -w -buildid=", "-o", str(output.resolve()), ".", cwd=source, env=env)
    output.chmod(0o755)
    output.with_suffix(output.suffix + ".sha256").write_text(hashlib.sha256(output.read_bytes()).hexdigest() + "\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source", type=Path, help="Local checkout of the exact upstream revision, for development")
    parser.add_argument("--work", type=Path, help="Keep prepared source at this new path")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--arch", choices=("amd64", "arm64"))
    parser.add_argument("--skip-tests", action="store_true", help="For cross-compilation only")
    args = parser.parse_args()
    temporary = tempfile.TemporaryDirectory(prefix="logger-native-")
    try:
        work = args.work.resolve() if args.work else Path(temporary.name) / "work"
        if work.exists():
            raise RuntimeError("Native work directory must be new.")
        if args.source:
            original = args.source.resolve()
            for path, expected in ((original, UPSTREAM), (original / "bridge-e2ee/meta", META)):
                actual = subprocess.check_output(["git", "-C", str(path), "rev-parse", "HEAD"], text=True).strip()
                if actual != expected:
                    raise RuntimeError("Development checkout revision mismatch.")
            shutil.copytree(original / "bridge-e2ee", work, ignore=shutil.ignore_patterns(".git", "build", "__pycache__"))
        else:
            repo = Path(temporary.name) / "upstream"
            checkout("https://github.com/m008v/fbchat-v2.git", UPSTREAM, repo)
            original = repo / "bridge-e2ee"
            checkout("https://github.com/mautrix/meta.git", META, original / "meta")
            shutil.copytree(original, work, ignore=shutil.ignore_patterns(".git"))
        patch(work, Path(__file__).resolve().parent / "native")
        if not args.prepare_only:
            build(work, args.output, arch=args.arch, test=not args.skip_tests)
    finally:
        temporary.cleanup()


if __name__ == "__main__":
    main()
