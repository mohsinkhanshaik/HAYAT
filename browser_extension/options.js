const token = document.querySelector("#token");
const enabled = document.querySelector("#enabled");
const status = document.querySelector("#status");
chrome.storage.local.get({pairingToken: "", enabled: true}, value => {
  token.value = value.pairingToken; enabled.checked = value.enabled;
});
document.querySelector("#save").addEventListener("click", async () => {
  await chrome.storage.local.set({pairingToken: token.value.trim(), enabled: enabled.checked});
  status.textContent = " Saved";
});
