"use strict";
(() => {
  const $ = id => document.getElementById(id);
  let csrf = "", active = false, ready = false, frameBusy = false, finished = false, finishing = false, viewGeneration = 0, viewFailures = 0, reconnectTimer = null, fallbackTimer = null, statusBusy = false, progressRequest = null, compatibility = false, errorKind = "";
  let invitation = location.hash.slice(1), composing = false, typingBoxOpen = false;
  const compactKeyboard = matchMedia("(max-width: 760px), (pointer: coarse)");
  const keyboard = new MessengerRemote.KeyboardFocus($("remote-typing"), window, () => compactKeyboard.matches && !typingBoxOpen);
  const syncKeyboard = () => {
    $("remote-input").classList.toggle("compact-keyboard", compactKeyboard.matches);
    $("remote-input").classList.toggle("typing-box-open", typingBoxOpen);
    $("remote-show-input").setAttribute("aria-expanded", String(typingBoxOpen));
    $("remote-show-input").textContent = typingBoxOpen ? "Hide typing box" : "Typing box";
    keyboard.sync();
  };
  compactKeyboard.addEventListener("change", syncKeyboard);
  syncKeyboard();
  const showError = (text, kind = "setup") => { errorKind = kind; $("setup-error").textContent = text; $("setup-error").hidden = false; };
  const hideError = () => { errorKind = ""; $("setup-error").hidden = true; };
  const inputs = new MessengerRemote.InputQueue(events => api("input", {events}), error => showError(error.message));
  function stopView() {
    viewGeneration += 1;
    frameBusy = false; statusBusy = false;
    clearTimeout(reconnectTimer); clearTimeout(fallbackTimer);
    reconnectTimer = null; fallbackTimer = null;
    $("remote-screen").onerror = null;
    const old = $("remote-screen").src;
    $("remote-screen").removeAttribute("src");
    if (old.startsWith("blob:")) URL.revokeObjectURL(old);
  }
  function resetInput() { inputs.clear(); composing = false; keyboard.cancel(); $("remote-typing").blur(); $("remote-typing").value = ""; }
  function setFinishing(value) {
    finishing = value;
    $("finish-facebook").disabled = value || finished || !ready || !$("approve-session").checked;
    $("finish-facebook").textContent = value ? "Connecting…" : "Connect & continue ↗";
    for (const id of ["remote-typing", "remote-keyboard", "remote-show-input", "remote-enter", "reload-browser", "start-browser"]) $(id).disabled = value;
  }

  addEventListener("hashchange", () => {
    const next = location.hash.slice(1);
    if (!next) return;
    invitation = next; csrf = ""; active = false; finished = false; ready = false; viewFailures = 0; setFinishing(false); stopView(); resetInput();
    hideError(); $("claim-form").reset();
    $("welcome").hidden = false; $("facebook").hidden = true; $("telegram").hidden = true; $("reconnect-confirm").hidden = true;
    $("remote-screen").hidden = true; $("remote-placeholder").hidden = false;
    $("step-welcome").classList.add("current"); $("step-facebook").classList.remove("current"); $("step-telegram").classList.remove("current");
  });
  async function api(action, body, anonymous = false) {
    const auth = csrf;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), action === "start" ? 50000 : action === "finish" ? 45000 : 20000);
    let response, data;
    try {
      response = await fetch("/setup/api/" + action, {method: body === undefined ? "GET" : "POST", credentials: "same-origin", cache: "no-store", signal: controller.signal, headers: {"Content-Type": "application/json", ...(anonymous ? {} : {"X-Setup-CSRF": auth})}, ...(body === undefined ? {} : {body: JSON.stringify(body)})});
      data = await response.json();
    } catch (error) { throw new Error("The connection was interrupted. Please try again."); }
    finally { clearTimeout(timeout); }
    if (!response.ok) {
      if (response.status === 401 && auth === csrf) { csrf = ""; active = false; finished = true; setFinishing(false); stopView(); resetInput(); $("remote-screen").hidden = true; showError(data.error || "This setup session has expired or was disabled. Ask for a new invitation."); }
      throw new Error(data.error || "Setup could not continue. Please try again.");
    }
    return data;
  }
  function progress(data) {
    if (finished && data.state !== "completed") return; // Ignore an older in-flight status response.
    csrf = data.csrf;
    finished = data.state === "completed";
    const reconnect = data.purpose === "reconnect";
    const waiting = reconnect && !data.confirmed && !finished;
    $("welcome").hidden = true; $("facebook").hidden = finished || waiting; $("telegram").hidden = !finished;
    $("reconnect-confirm").hidden = !waiting;
    if (waiting) {
      active = false; stopView(); resetInput();
      if (data.telegram_url && /^https:\/\/t\.me\/[A-Za-z0-9_]+$/.test(data.telegram_url)) $("reconnect-telegram").href = data.telegram_url;
    }
    $("step-welcome").classList.remove("current");
    $("step-facebook").classList.toggle("current", !finished); $("step-telegram").classList.toggle("current", finished);
    if (finished) {
      active = false; setFinishing(false); stopView(); resetInput(); hideError();
      $("telegram-invite").hidden = !data.telegram_url;
      if (data.telegram_url && /^https:\/\/t\.me\/[A-Za-z0-9_]+(?:\?start=[A-Za-z0-9_-]+)?$/.test(data.telegram_url)) $("telegram-invite").href = data.telegram_url;
      $("pairing-status").textContent = ({waiting: "Open Telegram above and press Start to connect your private chat.", pending_approval: "Telegram connected. Your administrator will approve your identity and start the logger.", approved: "Your Telegram destination is approved. Your administrator controls when capture starts.", expired: "The Telegram invitation expired. Ask your administrator for a new Telegram invitation."})[data.pairing];
      if (reconnect) {
        $("completion-title").textContent = "Messenger session updated";
        $("completion-description").textContent = "Your existing account was refreshed and the temporary browser has closed.";
        $("telegram-invite").textContent = "Return to Telegram";
        $("pairing-status").textContent = "Your logger is reconnecting. Check its connection with the bot's status command.";
        $("completion-note").textContent = "Your original Messenger account, Telegram pairing and saved settings are unchanged. No additional account was created.";
      }
    }
  }
  async function status(reportErrors = true) {
    const auth = csrf;
    if (progressRequest?.auth === auth) return progressRequest.promise;
    const pending = {auth};
    pending.promise = (async () => {
      try { const data = await api("status"); if (!invitation && auth === csrf) progress(data); return data; }
      catch (error) { if (reportErrors && !invitation && auth === csrf) showError(error.message); return null; }
      finally { if (progressRequest === pending) progressRequest = null; }
    })();
    progressRequest = pending;
    return pending.promise;
  }
  $("claim-form").addEventListener("submit", async event => {
    event.preventDefault(); hideError(); $("claim-button").disabled = true;
    try {
      const result = await api("claim", {token: invitation, name: $("guest-name").value, consent: $("guest-consent").checked}, true);
      invitation = ""; history.replaceState(null, "", location.pathname); progress(result);
    } catch (error) { showError(error.message); } finally { $("claim-button").disabled = false; }
  });
  function metadata(data) {
    viewFailures = 0;
    const screen = $("remote-screen");
    if (screen.getAttribute("width") !== String(data.width) || screen.getAttribute("height") !== String(data.height)) { screen.width = data.width; screen.height = data.height; }
    $("remote-site").textContent = data.site; ready = data.ready;
    if (errorKind === "view") hideError();
    $("facebook-ready").textContent = finishing ? "Saving this connection and preparing Telegram…" : ready ? "A signed-in session was detected. Confirm below when you have finished Facebook's checks." : "Finish signing in and complete any Facebook checks. Then confirm this connection here.";
    $("finish-facebook").disabled = finishing || !ready || !$("approve-session").checked;
  }
  async function viewStatus() {
    if (!active || finished || finishing || statusBusy || document.hidden) return;
    statusBusy = true; const generation = viewGeneration;
    try { const data = await api("view", {}); if (active && generation === viewGeneration) metadata(data); }
    catch (error) {
      if (active && !finishing && generation === viewGeneration) {
        showError(error.message, "view");
        if (++viewFailures >= 3) {
          active = false; ready = false; stopView(); resetInput(); setFinishing(false);
          $("remote-screen").hidden = true; $("remote-placeholder").hidden = false;
          showError("The temporary browser disconnected. Reopen it to continue. A saved connection will be recovered automatically.", "view");
        }
      }
    }
    finally { if (generation === viewGeneration) statusBusy = false; }
  }
  async function frame() {
    if (!active || finished || finishing || frameBusy || document.hidden || !compatibility) return;
    frameBusy = true; const generation = viewGeneration;
    try {
      const data = await api("frame", {});
      const bytes = Uint8Array.from(atob(data.jpeg), value => value.charCodeAt(0));
      const url = URL.createObjectURL(new Blob([bytes], {type: "image/jpeg"}));
      const decoded = new Image(); decoded.src = url;
      await decoded.decode();
      if (!active || generation !== viewGeneration) { URL.revokeObjectURL(url); return; }
      const previous = $("remote-screen").src;
      $("remote-screen").src = url; $("remote-screen").hidden = false; $("remote-placeholder").hidden = true;
      if (previous.startsWith("blob:")) URL.revokeObjectURL(previous);
      metadata(data);
    } catch (error) { if (active && !finishing && generation === viewGeneration) showError(error.message, "view"); }
    finally { if (generation === viewGeneration) { frameBusy = false; if (active && !finishing && compatibility && !document.hidden) fallbackTimer = setTimeout(frame, 250); } }
  }
  function startView(attempt = 0) {
    stopView();
    if (!active || finished || finishing || document.hidden) return;
    const generation = viewGeneration;
    if (compatibility) { frame(); return; }
    const screen = $("remote-screen");
    screen.onerror = () => {
      if (!active || finishing || document.hidden || generation !== viewGeneration) return;
      if (attempt >= 2) { compatibility = true; startView(); }
      else reconnectTimer = setTimeout(() => startView(attempt + 1), 400 * (attempt + 1));
    };
    screen.src = "/setup/api/stream?view=" + generation;
    screen.hidden = false; $("remote-placeholder").hidden = true;
    viewStatus();
  }
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stopView();
    else if (active && !finished && !finishing) startView();
  });
  $("start-browser").addEventListener("click", async () => {
    const auth = csrf;
    hideError(); $("start-browser").disabled = true; $("start-browser").textContent = "Opening your browser…";
    try { await api("start", {mobile: matchMedia("(max-width: 760px)").matches}); if (auth !== csrf || finished) return; active = true; compatibility = false; await viewStatus(); startView(); }
    catch (error) { if (auth === csrf) { await status(false); if (!finished) showError(error.message); } }
    finally { if (auth === csrf) { $("start-browser").disabled = finishing; $("start-browser").textContent = "Open Facebook ↗"; } }
  });
  function input(data) {
    if (!active || finished || finishing) return;
    inputs.push(data, data.action === "click" || data.action === "key" || data.action === "reload");
  }
  let touch = null, suppressClickUntil = 0;
  $("remote-screen").addEventListener("pointerdown", event => {
    if (event.pointerType !== "touch" || !active) return;
    suppressClickUntil = 0;
    touch = {id: event.pointerId, start: event.clientY, last: event.clientY, moved: false};
    event.currentTarget.setPointerCapture(event.pointerId);
  });
  $("remote-screen").addEventListener("pointermove", event => {
    if (!touch || touch.id !== event.pointerId) return;
    if (Math.abs(event.clientY - touch.start) > 6) touch.moved = true;
    if (touch.moved) {
      event.preventDefault();
      input({action: "scroll", delta: MessengerRemote.scrollDelta(event.currentTarget, touch.last - event.clientY)});
      touch.last = event.clientY;
    }
  });
  const finishTouch = event => {
    if (!touch || touch.id !== event.pointerId) return;
    if (touch.moved || event.type === "pointercancel") suppressClickUntil = Date.now() + 500;
    touch = null;
  };
  $("remote-screen").addEventListener("pointerup", finishTouch);
  $("remote-screen").addEventListener("pointercancel", finishTouch);
  $("remote-screen").addEventListener("click", event => {
    if (Date.now() < suppressClickUntil) return;
    const point = MessengerRemote.mapPoint(event.currentTarget, event.clientX, event.clientY);
    if (!point) return;
    input({action: "click", ...point});
    keyboard.focus();
  });
  $("remote-keyboard").addEventListener("click", () => { if (active && !finished && !finishing) keyboard.focus(); });
  $("remote-done").addEventListener("click", () => $("remote-typing").blur());
  $("remote-show-input").addEventListener("click", () => {
    typingBoxOpen = !typingBoxOpen;
    syncKeyboard();
    if (typingBoxOpen) keyboard.focus();
    else $("remote-typing").blur();
  });
  $("remote-typing").addEventListener("focus", () => $("remote-input").classList.add("keyboard-active"));
  $("remote-typing").addEventListener("blur", () => $("remote-input").classList.remove("keyboard-active"));
  const keys = new Set(["Backspace", "Delete", "Enter", "Tab", "Escape", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
  $("remote-typing").addEventListener("keydown", event => {
    if (composing || event.isComposing || event.keyCode === 229) return;
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "a") { event.preventDefault(); input({action: "key", key: "Control+A"}); }
    else if (keys.has(event.key)) { event.preventDefault(); input({action: "key", key: event.key === "Tab" && event.shiftKey ? "Shift+Tab" : event.key}); }
  });
  $("remote-typing").addEventListener("input", event => {
    if (composing || event.isComposing) return;
    const text = event.target.value; event.target.value = "";
    if (text) input({action: "text", text});
  });
  $("remote-typing").addEventListener("beforeinput", event => {
    if (composing || event.isComposing) return;
    const key = ({deleteContentBackward: "Backspace", deleteContentForward: "Delete", insertLineBreak: "Enter", insertParagraph: "Enter"})[event.inputType];
    if (key) { event.preventDefault(); input({action: "key", key}); }
  });
  $("remote-typing").addEventListener("compositionstart", () => { composing = true; });
  $("remote-typing").addEventListener("compositionend", event => { composing = false; const text = event.target.value; event.target.value = ""; if (text) input({action: "text", text}); });
  $("remote-screen").addEventListener("wheel", event => {
    if (!active) return;
    event.preventDefault();
    const unit = event.deltaMode === 1 ? 16 : event.deltaMode === 2 ? event.currentTarget.getBoundingClientRect().height : 1;
    const delta = MessengerRemote.scrollDelta(event.currentTarget, event.deltaY * unit);
    if (delta) input({action: "scroll", delta});
  }, {passive: false});
  $("remote-enter").addEventListener("click", () => input({action: "key", key: "Enter"}));
  $("reload-browser").addEventListener("click", () => input({action: "reload"}));
  $("approve-session").addEventListener("change", () => { $("finish-facebook").disabled = finishing || !ready || !$("approve-session").checked; });
  $("finish-facebook").addEventListener("click", async () => {
    if (finishing || finished || !$("approve-session").checked) return;
    const auth = csrf;
    hideError(); setFinishing(true); stopView();
    $("remote-screen").hidden = true; $("remote-placeholder").hidden = false;
    $("facebook-ready").textContent = "Saving this connection and preparing Telegram…";
    try {
      try { await inputs.drain(); }
      catch (inputError) {
        // Never replay an ambiguous login click. A fresh read-only check can
        // confirm that authentication succeeded despite its lost response.
        const view = await api("view", {});
        if (auth !== csrf) return;
        metadata(view);
        if (!view.ready) throw inputError;
        inputs.clear();
      }
      if (auth !== csrf || finished) return;
      const result = await api("finish", {approve: $("approve-session").checked});
      if (auth === csrf) progress(result);
    } catch (error) {
      if (auth !== csrf || finished) return;
      // The server may have committed the session and closed the browser
      // before the response arrived. Recover by reading state, never resending.
      try { const saved = await api("status"); if (auth === csrf) progress(saved); } catch (ignored) {}
      if (auth === csrf && !finished) showError(error.message);
    } finally {
      if (auth === csrf) { setFinishing(false); if (active && !finished) startView(); }
    }
  });
  $("cancel-setup").addEventListener("click", async () => {
    try { await api("cancel", {}); csrf = ""; active = false; finished = true; setFinishing(false); stopView(); resetInput(); $("facebook").hidden = true; showError("Setup cancelled. This invitation can no longer be used."); }
    catch (error) { showError(error.message); }
  });
  setInterval(viewStatus, 800);
  setInterval(() => { if (csrf) status(false); }, 3000);
  if (!invitation) status();
})();
