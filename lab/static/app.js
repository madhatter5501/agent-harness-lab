const $ = (id) => document.getElementById(id);
let token = "",
  runId = null,
  current = null,
  selected = -1,
  timer = null;
async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    headers: {
      "Content-Type": "application/json",
      ...(token ? { Authorization: "Bearer " + token } : {}),
      ...(options.headers || {}),
    },
  });
  const body = await response.json();
  if (!response.ok)
    throw Error(
      typeof body.detail === "string" ? body.detail : JSON.stringify(body),
    );
  return body;
}
function notice(text, error = false) {
  $("notice").textContent = text;
  $("notice").className = error ? "error" : "muted";
}
async function login() {
  const result = await api("/demo/session", {
    method: "POST",
    body: JSON.stringify({ user: $("identity").value }),
  });
  token = result.token;
  runId = null;
  current = null;
  $("followup").checked = false;
  await recent();
}
async function recent() {
  const rows = await api("/runs");
  $("recent").replaceChildren();
  for (const row of rows) {
    const button = document.createElement("button");
    button.textContent = row.prompt + " · " + row.status;
    button.onclick = () => {
      runId = row.id;
      selected = -1;
      poll();
    };
    $("recent").append(button);
  }
}
function select(index) {
  selected = index;
  const item = current.events[index];
  $("stepname").textContent = item.title;
  $("detail").textContent = JSON.stringify(item.data, null, 2);
  document
    .querySelectorAll(".step")
    .forEach((el, i) => el.classList.toggle("selected", i === index));
  const kind = ["tool_result"].includes(item.kind)
    ? "http"
    : [
          "context",
          "utility",
          "model_output",
          "classification",
          "memory",
        ].includes(item.kind)
      ? "model"
      : ["approval", "error"].includes(item.kind)
        ? "guard"
        : item.kind;
  document
    .querySelectorAll(".node")
    .forEach((el) => el.classList.toggle("active", el.dataset.node === kind));
}
function renderChat(run) {
  const log = $("chatlog");
  log.replaceChildren();
  let messages = [...(run.conversation || [])];
  if (
    ["running", "approval_required", "failed", "interrupted"].includes(
      run.status,
    )
  ) {
    messages.push({ role: "user", content: run.prompt });
    if (run.answer) messages.push({ role: "assistant", content: run.answer });
    else if (run.status === "approval_required")
      messages.push({
        role: "assistant",
        content:
          "Please review the exact action below. It has not been executed.",
      });
  }
  for (const message of messages) {
    const bubble = document.createElement("div");
    bubble.className = "bubble " + message.role;
    const label = document.createElement("small");
    label.textContent = message.role === "user" ? "You" : "Assistant";
    bubble.append(label, document.createTextNode(message.content));
    log.append(bubble);
  }
  log.scrollTop = log.scrollHeight;
  $("chatstatus").textContent =
    run.status === "needs_input"
      ? "Your reply is needed. Add the missing details below."
      : run.status === "approval_required"
        ? "Waiting for your approval or decline."
        : run.status === "running"
          ? "Working on your message…"
          : "You can ask a follow-up below.";
  $("followup").checked = true;
}
function render(run) {
  current = run;
  $("runref").textContent = run.id;
  renderChat(run);
  $("calls").textContent = run.calls;
  $("tokens").textContent =
    (run.usage.input_tokens + run.usage.output_tokens).toLocaleString() +
    (run.usage.missing_usage_calls ? " + unknown" : "");
  $("loaded").textContent = run.loaded.length;
  $("state").textContent = run.status.replaceAll("_", " ");
  $("state").style.fontSize = "16px";
  $("answer").textContent =
    run.answer ||
    (run.status === "approval_required"
      ? "Please review the proposed action. Nothing has been executed."
      : "Working…");
  $("approval").hidden = run.status !== "approval_required";
  $("pending").textContent = JSON.stringify(run.pending, null, 2);
  $("memory").textContent =
    run.summary ||
    "No summary needed yet. " +
      run.conversation.length +
      " recent conversation messages retained.";
  $("export").disabled = false;
  $("run").disabled = ["running", "approval_required"].includes(run.status);
  $("identity").disabled = run.status === "running";
  const previous = selected;
  $("timeline").replaceChildren();
  run.events.forEach((item, i) => {
    const button = document.createElement("button");
    button.className = "step";
    const num = document.createElement("span");
    num.className = "number";
    num.textContent = String(item.seq).padStart(2, "0");
    const text = document.createElement("span");
    text.textContent = item.title;
    const small = document.createElement("small");
    small.textContent =
      item.kind + " · " + new Date(item.time * 1000).toLocaleTimeString();
    text.append(small);
    button.append(num, text);
    button.onclick = () => select(i);
    $("timeline").append(button);
  });
  if (run.events.length)
    select(
      previous < 0
        ? run.events.length - 1
        : Math.min(previous, run.events.length - 1),
    );
}
async function poll() {
  clearTimeout(timer);
  if (!runId) return;
  try {
    const id = runId;
    const run = await api("/runs/" + id);
    if (id !== runId) return;
    render(run);
    if (run.status === "running") timer = setTimeout(poll, 700);
    else await recent();
  } catch (e) {
    notice(e.message, true);
    $("run").disabled = false;
  }
}
$("run").onclick = async () => {
  try {
    notice("Running. Local generation may take a moment.");
    $("run").disabled = true;
    const result = await api("/runs", {
      method: "POST",
      body: JSON.stringify({
        prompt: $("prompt").value,
        mode: $("mode").value,
        discovery: $("discovery").value,
        utility: $("utility").checked,
        previous_run: $("followup").checked ? runId : null,
      }),
    });
    runId = result.id;
    selected = -1;
    await poll();
    notice("Select any trace step to inspect it.");
  } catch (e) {
    notice(e.message, true);
    $("run").disabled = false;
  }
};
async function approval(value) {
  try {
    $("approve").disabled = true;
    $("decline").disabled = true;
    await api("/runs/" + runId + "/approval", {
      method: "POST",
      body: JSON.stringify({ approve: value }),
    });
    selected = -1;
    await poll();
  } catch (e) {
    notice(e.message, true);
  } finally {
    $("approve").disabled = false;
    $("decline").disabled = false;
  }
}
$("approve").onclick = () => approval(true);
$("decline").onclick = () => approval(false);
function clearConversation() {
  clearTimeout(timer);
  runId = null;
  current = null;
  selected = -1;
  $("followup").checked = false;
  $("chatlog").replaceChildren();
  $("chatstatus").textContent = "New conversation. What would you like to do?";
  $("prompt").value = "";
  $("answer").textContent = "Send a message to begin.";
  $("state").textContent = "Ready";
  $("calls").textContent = "—";
  $("tokens").textContent = "—";
  $("loaded").textContent = "—";
  $("approval").hidden = true;
  $("export").disabled = true;
  $("timeline").replaceChildren();
  $("detail").textContent = "No run selected.";
  $("memory").textContent = "No conversation yet.";
  $("run").disabled = false;
}
$("newchat").onclick = clearConversation;
$("prompt").onkeydown = (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    if (!$("run").disabled) $("run").click();
  }
};
$("identity").onchange = async () => {
  clearTimeout(timer);
  try {
    await login();
    clearConversation();
    $("run").disabled = false;
    $("approval").hidden = true;
    $("export").disabled = true;
    notice("Identity changed. Start a new run or select a saved run.");
  } catch (e) {
    notice(e.message, true);
  }
};
$("direct").onclick = async () => {
  try {
    $("directresult").textContent = JSON.stringify(
      await api("/direct/orders/" + encodeURIComponent($("directid").value)),
      null,
      2,
    );
  } catch (e) {
    $("directresult").textContent = e.message;
  }
};
$("export").onclick = async () => {
  try {
    const data = await api("/runs/" + runId + "/audit");
    const url = URL.createObjectURL(
      new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }),
    );
    const a = document.createElement("a");
    a.href = url;
    a.download = "audit-" + runId + ".json";
    a.click();
    URL.revokeObjectURL(url);
  } catch (e) {
    notice(e.message, true);
  }
};
document.querySelectorAll("[data-prompt]").forEach(
  (button) =>
    (button.onclick = () => {
      $("prompt").value = button.dataset.prompt;
    }),
);
(async () => {
  try {
    const c = await api("/config");
    $("modelname").textContent = c.model;
    await login();
  } catch (e) {
    notice(e.message, true);
  }
})();
