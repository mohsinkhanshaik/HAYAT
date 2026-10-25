const BRIDGE = "ws://127.0.0.1:8765";
let socket = null;
let reconnectTimer = null;

async function settings() {
  return chrome.storage.local.get({pairingToken: "", enabled: true});
}

async function connect() {
  const config = await settings();
  if (!config.enabled || !config.pairingToken || socket?.readyState === WebSocket.OPEN) return;
  socket = new WebSocket(BRIDGE);
  socket.onopen = () => socket.send(JSON.stringify({type: "authenticate", token: config.pairingToken}));
  socket.onmessage = async event => {
    const message = JSON.parse(event.data);
    if (message.type !== "command") return;
    try {
      const result = await execute(message.command, message.arguments || {});
      socket.send(JSON.stringify({type: "response", id: message.id, ok: true, result}));
    } catch (error) {
      socket.send(JSON.stringify({type: "response", id: message.id, ok: false, error: String(error)}));
    }
  };
  socket.onclose = () => { socket = null; scheduleReconnect(); };
  socket.onerror = () => socket?.close();
}

function scheduleReconnect() {
  clearTimeout(reconnectTimer);
  reconnectTimer = setTimeout(connect, 3000);
}

async function activeTab() {
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  if (!tab?.id || !/^https?:/.test(tab.url || "")) throw new Error("The active tab is not a public web page.");
  return tab;
}

async function execute(command, args) {
  const tab = await activeTab();
  if (command === "snapshot") {
    const [{result}] = await chrome.scripting.executeScript({target: {tabId: tab.id}, func: () => {
      const visible = document.body?.innerText || "";
      const links = [...document.querySelectorAll("a[href]")].slice(0, 500).map(a => ({text: (a.innerText || "").trim().slice(0, 300), href: a.href}));
      const forms = [...document.forms].slice(0, 100).map(form => ({action: form.action, method: form.method,
        fields: [...form.elements].slice(0, 100).map(field => ({name: field.name, type: field.type, label: field.getAttribute("aria-label") || ""}))}));
      return {url: location.href, title: document.title, text: visible.slice(0, 500000), links, forms,
        viewport: {width: innerWidth, height: innerHeight, scrollY, documentHeight: document.documentElement.scrollHeight}};
    }});
    return result;
  }
  if (command === "scroll") {
    const [{result}] = await chrome.scripting.executeScript({target: {tabId: tab.id}, args: [args], func: options => {
      const amount = Math.max(1, Math.min(Number(options.amount) || 700, 5000));
      if (options.direction === "top") scrollTo({top: 0, behavior: "smooth"});
      else if (options.direction === "bottom") scrollTo({top: document.documentElement.scrollHeight, behavior: "smooth"});
      else scrollBy({top: options.direction === "up" ? -amount : amount, behavior: "smooth"});
      return {url: location.href, scrollY, documentHeight: document.documentElement.scrollHeight};
    }});
    return result;
  }
  throw new Error(`Unsupported command: ${command}`);
}

chrome.runtime.onInstalled.addListener(() => chrome.runtime.openOptionsPage());
chrome.storage.onChanged.addListener(connect);
connect();
