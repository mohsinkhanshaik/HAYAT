# Web3 explorer and the agentic web (Web 4.0)

Open it from **Studios → Web3 explorer & agentic web (Web 4.0)**, or ask in chat.

Everything here is read-only. HAYAT reads public chain data and public website
files. It has no wallet, never signs, never sends a transaction and never logs
in. Asking it to send, buy, sell or swap crypto is stopped by the money guard
before any tool runs.

## Networks & blocks

- **Bitcoin** (mempool.space):
  - fees (fastest, within an hour, economy) and waiting transactions;
  - mining hashrate and last week's pools;
  - the next difficulty adjustment, with how far the epoch has gone;
  - the Lightning network (nodes, channels, capacity, with its date).
- **Ethereum, Optimism and zkSync Era** (Blockscout): gas (slow, average, fast),
  block time, the share of capacity used, transactions today and addresses.
- **Fees while the window is open:** Bitcoin's fastest fee and Ethereum's average
  gas, drawn every 30 seconds.
- **Live block strips:** the newest Bitcoin blocks, plus the newest blocks of
  the chain you pick (Ethereum, Optimism or zkSync). A new block is outlined and
  marked "new". Click a block to open it in the explorer.

**Not read:** Base, Arbitrum and Polygon. Their Blockscout explorers answer
automated requests with a browser challenge ("Just a moment…"), and HAYAT does
not get around that. Their value locked still appears in the DeFi tab, from
DefiLlama.

## Explorer

Type, paste or click any of these:

- a Bitcoin address (bc1…, 1…, 3…) or an Ethereum address (0x…, 40 hex);
- a transaction hash: 64 hex characters for Bitcoin, 0x plus 64 for Ethereum;
- an ENS name such as `vitalik.eth`;
- a block, such as `bitcoin block 900000`, `ethereum block 20000000` or
  `zksync block 5`.

| What | Shows |
|---|---|
| Address | Balance (and pending), transaction count, contract or token details, the latest 25 transactions with the change for this address |
| Transaction | Confirmed or waiting, confirmations, block, value, fee and fee rate, inputs and outputs (Bitcoin), sender, receiver, method and token transfers (Ethereum) |
| Block | Time, transactions, miner or pool, fees and reward, gas used |

How to use it:

- Double-click a row, or press **Open selected**, to follow a transaction or
  address.
- ◀ and ▶ go back and forward, like a browser.
- **Copy selected** puts a hash on the clipboard.
- The chain box picks Optimism or zkSync for an 0x address. It is Ethereum
  otherwise.
- **★ Watch this address** adds the address on screen to your watched
  addresses.
- **Tokens held** (Ethereum, Optimism, zkSync) are listed first, ◆, with Blockscout's price:
  - hidden: tokens with no price, tokens Blockscout flags, and holdings worth more than 1 % of
    a token's whole market value (these are mostly airdropped or too illiquid to be worth
    that);
  - double-click a token to open its contract.
  - The total is Blockscout's estimate, not a sale price.

## ★ Watched addresses

- Addresses you follow, with a label. They are kept on this computer in
  `data\web3.json`.
- While this tab is open, balances are read again every two minutes. **Check
  now** reads them at once.
- A balance that changed since the last check is marked "● changed", in green
  or red, with the amount. The window chimes once.
- Double-click an address, or press **Open in explorer**, to see its
  transactions. **Stop watching** removes it.
- At most 50 addresses. A transaction or block cannot be watched. An entry you
  renamed is still found by its ENS name.

Watching only reads public balances. Nothing is signed or sent.

## DeFi

- Value locked per chain as bars (DefiLlama). Hover a bar for its figure and
  share.
- The largest stablecoins by supply, with what backs them and on how many
  chains.

Value locked is DefiLlama's estimate of funds deposited in DeFi apps.

**Decentralised exchanges:**
- what was traded in 24 hours and 7 days;
- the change on the day before;
- the 20 largest exchanges with their volume, change (green or red) and number of chains.

## Agentic web (Web 4.0)

"Web 4.0" has no agreed definition. HAYAT uses it for the **agentic web**: what
a website publishes for AI agents, and which AI crawlers it lets in. Enter a
site and press **Check site**. HAYAT reads these public files:

| Surface | What it means |
|---|---|
| robots.txt | The site's crawler rules |
| Rules naming AI agents | The site's own position on AI crawlers, or Content-Signal preferences (search / ai-input / ai-train) |
| llms.txt, llms-full.txt | A Markdown guide to the site for language models |
| A2A agent card (`/.well-known/agent-card.json`, older `agent.json`) | The site runs an agent other agents can call |
| MCP manifest (`/.well-known/mcp.json`, a proposal) | Tools an AI assistant can use |
| AI plugin manifest (older) | The 2023 ChatGPT plugin format |
| schema.org structured data, RSS/Atom feed, sitemap | Facts and pages labelled for machines |
| security.txt | Who to contact about a security problem |

Reading the results:

- **The ring** counts the 8 main surfaces published. It counts what exists,
  not its quality.
- **The crawler table** shows, for 12 AI agents (GPTBot, ClaudeBot,
  Google-Extended, PerplexityBot, CCBot and others), whether robots.txt lets
  them read the site root. It also shows whether the site names each one.
- **Select a row** to see what it means and a preview of the file.
- **Policy by purpose:** the crawler rules are summed up by what the AI is for: training,
  search answers, and fetching a page a person asked for. Each is allowed, blocked or mixed.
  For example, nytimes.com: training blocked; search mixed; on request blocked.
- **What the site could add:** the missing surfaces, most useful first (robots.txt, AI rules,
  llms.txt, structured data, sitemap, an agent card or MCP server, security.txt).
- **Checked before:** the last 20 sites you checked, with score, AI-crawler
  stance and time. Click one to check it again. The list is kept in
  `data\web3.json`.

**Keys:**
- Ctrl+1 to Ctrl+5 switch tabs.
- Ctrl+L goes to the explorer search.
- Alt+← and Alt+→ go back and forward.
- F5 refreshes.

The Trade Center's Web3 panel links here ("Open the Web3 explorer ↗").

**How the files are read:**

- Every file is read with HAYAT's own user agent, honouring robots.txt. A file
  robots.txt asks agents not to read is reported as not read.
- A page that answers every address with its ordinary HTML counts as "not this
  file", never as published.

Measured on 2026-10-11:

| Site | Surfaces | AI crawlers |
|---|---|---|
| nytimes.com | 3/8 | blocks 11 of 12 |
| github.com | 3/8 | blocks Bytespider only; publishes llms.txt |
| stripe.com | 4/8 | allows all; publishes llms.txt |
| example.com | 0/8 | no stated position |

## Ask in chat

| You say | You get |
|---|---|
| "Look up vitalik.eth", or a pasted address or transaction hash, or "bitcoin block 900000" | The explorer's answer |
| "Latest bitcoin blocks", "recent eth blocks" | The newest blocks |
| "Bitcoin hashrate", "lightning network", "layer 2 gas", "next difficulty adjustment" | The networks |
| "How much is locked in DeFi?", "biggest stablecoins" | DeFi totals |
| "Watch vitalik.eth", "watch address 0x… as savings", "check my watched addresses", "stop watching vitalik.eth" | Your watched addresses, and what changed |
| "Is stripe.com ready for AI agents?", "does nytimes.com block AI crawlers?", "web 4.0 check for github.com" | The agent-web check |

"Bitcoin fees", "ETH gas" and "web3 stats" still give the short network state
from the Trade Center tools.

## Sources

| Source | Used for | Notes |
|---|---|---|
| mempool.space | Bitcoin fees, blocks, addresses, transactions, mining, Lightning | Keyless |
| eth.blockscout.com, zksync.blockscout.com, explorer.optimism.io | Ethereum, zkSync and Optimism state, blocks, addresses, transactions, ENS | Keyless; robots.txt allows the API |
| DefiLlama (api.llama.fi, stablecoins.llama.fi) | Value locked, stablecoins | Keyless; no robots.txt restriction |
| The site itself | Agent-web files | robots.txt honoured |

Code:
- data: [web3](../app/web3.py) and [web4](../app/web4.py);
- chat and tools: [web3_chat](../app/web3_chat.py);
- window: [gui_web3](../app/gui_web3.py);
- tests: [test_web3](../tests/test_web3.py).
