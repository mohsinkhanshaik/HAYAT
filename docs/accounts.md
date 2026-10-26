# Accounts & sign-in

Open **Studios → Accounts & sign-in (Google, Microsoft, GitHub…)**. Connect your
own accounts, then read your mail, calendar, files, repositories and channels.
You can also send or create things once you confirm.

The window groups the accounts as **Mail & calendar**, **Social**, **Messaging** and **Work**, each heading
saying how many are connected, with a **Find** box above the list.

| Account | What HAYAT can work with | Ways to sign in |
|---|---|---|
| Google | Gmail, Google Calendar, Google Drive, your YouTube channel and uploads | Browser |
| Microsoft (work, school or personal) | Outlook mail, Outlook calendar, OneDrive | Code, browser |
| GitHub | Repositories, notifications, issues | Code, browser, token |
| LinkedIn | Your profile; posting to your feed | Browser |
| Notion | Pages and databases you share with the integration | Token |
| Slack | Channels, channel history; posting messages | Token, incoming webhook |
| Bluesky | Your timeline and notifications; posting (300 characters) | App password (Settings → App passwords) |
| Mastodon | Your home timeline and notifications on your server; posting | Server name and access token |
| Reddit | Your inbox, joined subreddits, hot posts; posting to a subreddit | Browser (your own installed app) |
| X (Twitter) | Your profile; posting (280 characters) | Browser (free tier) |
| Telegram | Messages sent to your bot; the bot messaging you | Bot token from @BotFather |
| Discord | Posting to a channel; with a bot, servers and channel messages | Incoming webhook, bot token |
| Facebook Page & Instagram | Your Page's posts and your Instagram posts; posting to the Page | Page access token and Page ID |
| Threads | Your Threads posts; posting (500 characters) | Access token from your Meta app |

### Social accounts (2026-10-17)

The same rules hold for every social account:
- **Signing in:** you sign in on the platform's own page, or paste a token the platform made for apps.
  Bluesky's app password is one of these: made for one app, removable at any time, and not your
  account password. HAYAT keeps only the session Bluesky gives back.
- **Reading** runs directly.
- **Posting** is only prepared, and waits for you in the confirmation window.

**Post everywhere…** (in the window's header, or "post everywhere: …" in chat) prepares the same text
for every connected account that can post. Each post waits for its own confirmation. An account the
text doesn't fit (Bluesky 300 characters, X 280, Threads and Mastodon 500) is left out, with the reason.

**Tested hard (2026-10-17).** An attack run found eight problems, all fixed and now covered by
`tests/test_hard_social_and_research.py`:
- **Token leak:** a network error quoted the address it failed on, and with it Telegram's bot token. Failures
  now name only the host; webhook keys are treated the same way.
- **Local servers:** names like `router.lan.home`, `127.0.0.1.nip.io` and `169.254.169.254.nip.io` were accepted
  as Mastodon servers. A server must now resolve only to public internet addresses. This is checked again at
  every use, so a name pointed inside the network later is refused too. Live, `localtest.me`, a public name for
  this computer, was caught by that address check.
- **Lengths:** Bluesky now counts what you see (one family emoji is one character, not seven), and X uses its own
  weights (emoji and Chinese count as two, a link as 23). "Post everywhere" measures each network its own way.
- **Odd replies:** a reply in a shape the documentation doesn't promise, or one that isn't JSON, now gives a plain
  error instead of a crash. A fuzz test throws random replies and status codes at every read and post of
  every account.
- **Duplicates:** asking for the same post twice now leaves one waiting confirmation, not two.

**Not offered, and why:**
- **TikTok:** only audited apps may post, so a personal app's posts stay private drafts.
- **Pinterest:** each app must be approved before it can make pins.
- **Snapchat:** no posting API for individuals.
- **Instagram posting:** it publishes only from a public image address on a business account.
  HAYAT reads your Instagram posts through your Facebook Page instead.

## The ways to sign in, easiest first

1. **Sign in with a code** (OAuth 2.0 device authorization):
   - Press **Get my code**. HAYAT shows a short code in large type, copies it,
     and opens the provider's sign-in page.
   - Sign in there, paste the code and approve. HAYAT notices within seconds.
   - Offered for GitHub and Microsoft.
2. **Sign in with your browser** (OAuth 2.0 with PKCE):
   - The provider's page opens in your browser. After you approve, it returns
     to a one-time listener on this computer only (127.0.0.1).
   - The listener checks a random state value, so a forged return is ignored.
     It closes as soon as it is used, or after five minutes.
3. **Paste a token:**
   - For GitHub (fine-grained personal access token), Notion (integration
     secret) and Slack (bot token).
   - **Make a token ↗** opens the right page, and the numbered steps say what
     to choose.
   - HAYAT checks the token with one read before keeping it. A token that
     doesn't work is not kept.

**In every way, you type your password only on the provider's own page.**
HAYAT never sees it. An "app password" (Bluesky) is a separate password you
make on the provider's site for HAYAT alone, never your login password; the
window says so beside the box.

### Browser sign-in for every platform (2026-10-26)

**Sign in with your browser** opens the provider's page in a tab; you sign in there (your password never reaches
HAYAT), approve, and the tab comes back to HAYAT on this computer. HAYAT exchanges the code, reads the account to
confirm it, shows the real result in the tab, and keeps the sign-in in Windows Credential Manager. It stays signed
in across restarts and renews itself before it expires (Google, Microsoft, X, Reddit refresh tokens; Mastodon and
GitHub tokens do not expire).

The button had been greyed out until a developer app was registered, so it looked broken. Now:
- **Mastodon: no setup at all.** Type your server and press the button: HAYAT registers itself with your server
  (Mastodon allows any app to), opens the tab and comes back signed in.
- **Google, Microsoft, GitHub, Reddit, X, LinkedIn** need an app registration once. With **HAYAT's own app**
  (`config/oauth_apps.yaml`, see below) it is one click for everyone and the card says "HAYAT's own app: just sign
  in". Without one, the button reads **Set up, then sign in**: it opens the steps (GitHub's form comes filled in;
  the exact redirect address has a **Copy** button), and **Save app** opens the sign-in by itself.
- **Bluesky** keeps the app password (one tab, paste once): its browser sign-in for desktop apps needs DPoP-bound
  tokens HAYAT does not have yet. **Facebook, Instagram, Threads** need a Meta app with review for each permission,
  so they keep their tokens; **Telegram** uses a BotFather token; **Discord** a webhook or bot token.

#### HAYAT's own apps (the project maintainer, once)

Register each app under the project's own account and put the **client ID** in `config/oauth_apps.yaml`; it is not
a secret (every sign-in link carries it). A **client secret** never goes in the repository: keep it in
`data/integrations/oauth-apps.json` (ignored by git), e.g. `{"google": {"client_id": "…", "client_secret": "…"}}`.
The steps are the same as each provider's one-time setup in the window; a user's own app always wins.

| Provider | App type | Redirect address |
|---|---|---|
| Microsoft | Entra public client, personal + work accounts, public client flows on | `http://localhost` |
| GitHub | OAuth app, Enable Device Flow (code sign-in needs only the ID) | `http://127.0.0.1/` |
| Google | Desktop app (secret in the local file) | none needed |
| Reddit | installed app (no secret) | `http://127.0.0.1:8798/` |
| X | Native App, OAuth 2.0 PKCE | `http://127.0.0.1:8797/` |
| LinkedIn | app on a company Page (secret in the local file) | `http://127.0.0.1:8796/` |

### Email with your normal login (2026-10-26)

**Email (any provider)** connects your mailbox the way any mail app does: your address and password, over
IMAP and SMTP. No developer app, client ID or API key (`app/accounts_mail.py`).
- Zoho, GMX, AOL, Yandex, Fastmail, Rediffmail, company and own-domain mail: your normal password.
- Gmail, Yahoo, iCloud: these providers refuse normal passwords from mail apps; make an app password in the
  account's security settings (the window links it). Google's "abcd efgh ijkl mnop" spaces are fine.
- Outlook, Hotmail, Live: Microsoft turned password logins off for mail apps in 2024; use **Microsoft → Sign in
  with a code** instead. Proton Mail needs Proton's Bridge app.

HAYAT logs in once to check before keeping anything; the password goes to Windows Credential Manager, never a
file or the chat. Then: newest mail, unread mail, search (reading never marks mail as read), and **Send an
email**, which waits for your confirmation. The same login also sets up sending from chat and Business.

HAYAT does not type your passwords into websites or drive your logged-in social accounts with a scripted browser:
sites forbid it and it would put the accounts at risk. Social accounts keep their own simple ways (Bluesky app
password, Mastodon token, and so on).

### Confirming a connection (2026-10-25)

"Connected" used to be a note written at sign-in, and nothing confirmed it:
- The browser tab said "HAYAT is connected" the moment the provider sent it
  back, before HAYAT had exchanged the code for a token. If the exchange then
  failed (a wrong client secret, an expired code), the tab still claimed
  success.
- A test had left a fake "Bluesky connected" record in the live accounts file
  with no sign-in stored, and the window showed it as connected.

Now:
- **The tab shows the real result.** It waits until the token is exchanged and
  the account read, then says "HAYAT is connected to Google as you@…" or why
  it could not finish.
- **Every connection is checked.** Opening Accounts & sign-in reads each
  connected account once (whose account the sign-in belongs to) and shows the
  result on its card: connected, or "sign-in not working" with the reason.
  **Check now** repeats it. A sign-in missing from Windows Credential Manager
  is no longer shown as connected.
- **A tab fallback while signing in with the browser:**
  - **Open it again ↗** and **Copy the sign-in link**: paste the link into a
    tab of the browser where you are already signed in to that service.
  - If you approve but the tab says it cannot reach this computer (127.0.0.1
    or localhost: another browser, a security tool, a remote desktop), copy
    the whole address from that tab's address bar and paste it under
    **Finish sign-in**. HAYAT checks it belongs to this sign-in (its state
    value) and finishes. An address from another sign-in is refused.
  - **Cancel** stops waiting.
- **Keyboard:** the first empty box has focus, **Tab** moves to the next box
  and **Enter** connects from any of them.
- In chat: "is my bluesky connected?", "check my accounts", "am I signed in
  to github" make the same live check (`account_check`).
- Tests can no longer touch the live accounts file: the run fails if
  `data/integrations/accounts.json` changes.

**More ways for Slack:**
- **Incoming webhook:** the simplest way to post to one channel. Paste the
  webhook address Slack gives you. It can post but read nothing.
- **Bot token:** reads channels and their latest messages, and posts.

**LinkedIn:**
- Its self-made apps may read your own profile and post as you. They cannot
  read your feed, messages or connections, and HAYAT says so.
- LinkedIn signs in without PKCE and returns only to the exact address
  registered: http://127.0.0.1:8796/ (8765 is HAYAT's browser-extension
  bridge).
- A sign-in lasts about 60 days.

## One-time app setup

Code and browser sign-in need an app registered in your own Google, Microsoft or
GitHub account. That's how these providers let any program sign in. HAYAT shows
numbered steps and **Open the setup page ↗**. You register the app and paste
its Client ID (and, for Google and GitHub browser sign-in, its client secret).
HAYAT does not register apps or accounts for you.

**Google:** while your app's consent screen is in Testing:
- Google warns that the app is unverified. It is your own app, so choose
  Continue.
- Google ends the sign-in after 7 days. Press **Publish app** on the consent
  screen for a lasting one.

The progress bar at the top shows where you are: ① App setup → ② Sign in →
③ Work with it.

## Working with an account (Work with it)

**Reading** (buttons): latest Gmail or Outlook messages (● marks unread),
upcoming events, recent Drive or OneDrive files, GitHub repositories,
notifications and assigned issues, Notion search and Slack channels.
- Select a result to see its details.
- Double-click it to open it in your browser.

**Sending or creating** (form): a Gmail or Outlook email, a calendar event, a
GitHub issue or a Slack message.
- **Prepare for my confirmation** sends nothing.
- The confirmation window shows the account and every value. It is sent only
  when you confirm there, or say "confirm action N" at this computer.
- It runs once, within ten minutes, and the result comes back for you to mark
  right or wrong.

## Ask in chat

| You say | You get |
|---|---|
| "Check my gmail", "show my outlook inbox" | The latest messages |
| "My google calendar", "my outlook calendar" | Upcoming events |
| "Recent google drive files", "recent onedrive files" | Recent files |
| "My github notifications", "show my github repos", "github issues assigned to me" | GitHub |
| "Search notion for invoices", "my slack channels" | Notion, Slack |
| "Do I have new emails in gmail?", "anything new on github?", "what's on my outlook calendar today" | The same reads, in everyday words |
| "Post to linkedin: We launched!", "post to slack #general: standup in 5" | A prepared post in the confirmation window; nothing is sent until you confirm |
| "Post to bluesky: …", "tweet: …", "toot: …", "post to threads: …", "post to my facebook page: …", "post to discord: …" | The same, for that account |
| "Telegram me: the report is ready" | A prepared message from your bot to whoever last wrote to it |
| "Post everywhere: we launched!" | One prepared post per connected social account |
| "My bluesky notifications", "my mastodon timeline", "my reddit inbox", "what's hot in r/python", "telegram messages", "my youtube channel", "my latest youtube videos", "my instagram posts" | The matching reads |
| "Which accounts are connected?" | Every account's state |
| "Connect my google account", "sign in to github" | How to connect it (signing in happens in the window, on the provider's page) |

## Where things are kept

- **Tokens:** in the secret broker, which uses Windows Credential Manager (or an
  encrypted file on systems without it):
  - each token is scoped to its account;
  - it reaches a request only through a single-use lease;
  - it never enters a log, the model's context or a tool result.
- **Refresh:** access tokens are refreshed automatically before they expire.
- **Plain record:** `data\integrations\accounts.json` holds only which account
  is connected, as whom, how and since when, plus the Client ID. It holds no
  secrets.
- **Sign out:** deletes the token, and revokes it with Google.

The window also shows HAYAT's other connections: email sending (Business →
Email setup), Keys for cameras and stock prices, and the credential store.

Code: [accounts](../app/accounts.py), [chat](../app/accounts_chat.py),
[window](../app/gui_accounts.py), tests in
[test_accounts](../tests/test_accounts.py).
