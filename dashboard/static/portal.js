"use strict";
(() => {
  const root = document.getElementById("portal-login");
  if (!root) return;
  const copy = JSON.parse(root.dataset.copy);
  let token = location.hash.slice(1), csrf = "", timer = null, busy = false, approved = false;
  const byId = id => document.getElementById(id);
  const error = () => {
    byId("login-error").textContent = copy.login_error;
    byId("login-error").hidden = false;
  };
  if (/^[A-Za-z0-9_-]{43}$/.test(token)) {
    root.querySelectorAll(".portal-language a").forEach(link => { link.hash = token; });
    byId("login-request").hidden = false;
  } else token = "";
  history.replaceState(null, "", location.pathname + location.search);
  const api = async (path, options = {}) => {
    const response = await fetch("/me/api/" + path, {credentials: "same-origin", cache: "no-store", ...options});
    const data = await response.json();
    if (!response.ok) throw new Error("Request unavailable");
    return data;
  };
  const check = async (silent = false) => {
    if (busy || approved) return;
    busy = true;
    clearTimeout(timer);
    try {
      const data = await api("login-status");
      csrf = data.csrf;
      byId("login-request").hidden = true;
      byId("login-error").hidden = true;
      byId("login-pending").hidden = data.state === "approved";
      byId("login-approved").hidden = data.state !== "approved";
      byId("login-code").textContent = data.code;
      if (data.state === "approved") {
        approved = true;
        byId("login-name").textContent = data.name;
        byId("login-owner").textContent = data.owner;
      } else if (!document.hidden) timer = setTimeout(() => check(), 2500);
    } catch {
      if (!silent) error();
    } finally { busy = false; }
  };
  byId("request-login").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      await api("claim", {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({token})});
      token = "";
      root.querySelectorAll(".portal-language a").forEach(link => { link.hash = ""; });
      await check();
    } catch {
      // Read after an ambiguous response, never repeat the consuming claim.
      await check();
      if (!csrf) error();
    }
  });
  byId("check-login").addEventListener("click", () => check());
  byId("complete-login").addEventListener("click", async event => {
    event.currentTarget.disabled = true;
    try {
      const data = await api("complete", {method: "POST", headers: {"X-Portal-CSRF": csrf}});
      location.assign(data.redirect);
    } catch {
      // A session cookie may precede a lost reply. Do not repeat completion.
      location.assign("/me");
    }
  });
  document.addEventListener("visibilitychange", () => {
    clearTimeout(timer);
    if (!document.hidden && csrf && !approved) check();
  });
  if (!token) check(true);
})();
