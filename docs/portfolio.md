# Portfolio & net worth (USD)

Everything you own in one number, in US dollars: cash in any currency, stocks and funds, and crypto, valued live.
Open **Studios → Portfolio & net worth (USD)**, or ask in chat.

## What HAYAT does, and what it never does

HAYAT **keeps a record and values it**. It **does not hold** money, shares or coins, it **never trades**, and it
**keeps no keys**. Your bank, broker and exchange hold your assets; a buy or sell recorded here is one you made
there. Holding assets for someone needs a bank, broker-dealer or custodian licence, and holding crypto means
holding private keys, which turns any web page HAYAT reads into a risk to your money. A private key, seed phrase
or password pasted anywhere in the portfolio is refused and never stored.

## Recording what you hold

| Say | What is recorded |
|---|---|
| "I have 2500 USD in my Chase account" | cash, opening balance (`set`) |
| "I hold 0.25 BTC at 58000 on Coinbase" | crypto, opening balance with its cost |
| "I bought 5 nvidia shares at 120 on Zerodha" | stock buy (company names become tickers) |
| "I sold 2 AAPL at 210" | stock sell, profit realised at the average cost |
| "add address bc1… to my portfolio" | a public Bitcoin or Ethereum address, read from the chain |
| "remove holding 3" | takes a holding off the record |
| "show my portfolio", "what is my net worth?" | the valuation |

The window's form does the same for any move: **buy, sell, deposit, withdraw** or **set** an opening balance, with
the price in USD, a fee, the account and the date. Accounts are named ("HDFC savings ····1234"), never numbered:
a run of 6 or more digits is refused. A sale bigger than the recorded holding, or a date in the future, is refused.

"Watch address X" on its own still adds the address to the Web3 explorer's watch list; say "to my portfolio" to
count it here.

## How it is valued

* **Cash:** the day's reference rate from the European Central Bank (via Frankfurter), with ExchangeRate-API
  (www.exchangerate-api.com) for currencies the ECB does not publish (AED, SAR, PKR…). Rates are cached for six
  hours. If neither source answers, foreign cash is listed as *unpriced* rather than guessed.
* **Crypto:** Kraken's live USD price.
* **Stocks and funds:** Kraken's tokenized share, which tracks the exchange price around the clock but can drift
  from it, most when the US market is closed. With a free Finnhub key (Studios → Keys) the exchange's own price is
  used instead. A symbol neither source knows is listed with the reason, never valued at zero silently.
* **Watched addresses:** the balance is read from mempool.space (Bitcoin) or Blockscout (Ethereum) each time; for
  Ethereum, priced ERC-20 tokens are added (airdropped and illiquid tokens are left out, as in the Web3 explorer).
* **Cost and gains:** average cost per holding. Buys add quantity and cost (with fees); sells realise profit or
  loss against the average and reduce the cost proportionally. Unrealised gain = value now − remaining cost.

## Net worth over time

Each time the portfolio is read, today's net worth is saved (one point per day, the latest reading wins). The
window draws the line; chat says "Since 2026-10-07: $61,200.00 → $64,900.00".

## The business wallet

The business wallet's balances are converted to USD and shown next to net worth, but **not added to it**: that
money sits in your own bank accounts, which you may already have recorded as cash.

## Where it is kept

`data/memory/portfolio.db` (SQLite): holdings, moves and daily snapshots. Nothing leaves the computer except the
public price, rate and blockchain reads above.
