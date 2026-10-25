# Markets and the Trade Center

HAYAT shows the crypto markets. It never trades. There is no order button, no
wallet, no exchange account and no key anywhere in it. Asking it to buy, sell,
swap, stake or send crypto is stopped before any tool runs, with the full
explanation every money request gets ([money guard](../app/safety/money_requests.py)).

## GUI and forecast reliability (2026-10-17)

The Trade Center now fits smaller windows. **Layout** offers Auto, Chart focus,
Markets + chart, and Chart + details. Auto removes side panes as the window
narrows; short windows use a compact header. Drag the pane dividers to adjust
space. Auto gives Signals, 3D and Heatmap more center space. Compact signal
cards keep the results table visible, with details in the scrolling explanation.
Outlook and signal explanations scroll, and both signal tables have
horizontal and vertical scrollbars. Market-list refreshes preserve the selection
and reading position. Superseded queued searches are skipped, and old responses
or errors cannot overwrite a newer asset, range, quote currency or search.

The hourly outlook excludes unfinished candles, invalid prices and duplicate
timestamps. Missing hours are never compressed into consecutive hours: only the
latest uninterrupted run is used. Stale or insufficient history is shown plainly.
Ranges anchor to the last completed hourly close. The dollar outlook cone is
drawn only on dollar charts with current history.

Each hour/day/week range has its own non-overlapping historical outcomes.
After 20 earlier errors, finite-sample quantiles of standardized errors calibrate
the range. Later tests are scored before their outcome enters calibration.
Fewer than 20 later tests are labelled limited evidence; with too few calibration
cases, the original volatility baseline remains. The provider's short hourly
history means daily and especially weekly evidence is usually limited.

Momentum is compared against a past-only majority-direction baseline using
paired results, rather than treating 50% as the right reference for every market.
Signal adjustments are withheld unless a combination fitted on the earlier
two-thirds improves Brier score on at least ten completed later-period tests.
Current odds use that same fitted combination. Averaging shrunk log-odds shifts
prevents repeated correlated readings from multiplying identical evidence.

These changes improve data handling and evaluation; they do not establish live
directional accuracy or profitability. Synthetic checks show both improvements
and limitations. See the implementation and benchmark notes.

## Live, stocks, outlook and world events (2026-10-11)

**Live prices.** The coin or stock on screen streams from Kraken's public WebSocket
as each trade happens. A green **LIVE** badge shows the measured delay from the
exchange's trade time to its arrival here: a median of 130–135 ms over 85 live
trades, including this computer's clock offset. HAYAT shows the measured delay
rather than claiming zero. The stream starts when a price is asked for and stops
after five idle minutes.

**Stocks.** Every free stock source HAYAT tried refuses automated access in its
robots.txt: Stooq, Nasdaq, Yahoo Finance, CNBC and Twelve Data. HAYAT honours that,
so stock prices come from two places:
- **Kraken's tokenized shares (xStocks)**, keyless and live: about 177 US shares and
  funds, including sector and country funds (S&P 500, Nasdaq-100, energy,
  semiconductors, gold, copper, defence, uranium, Germany, Korea, Europe and others).
  A tokenized share tracks the real stock but trades on its own, around the clock, so
  it can drift from the exchange price, most when the US market is closed. Shares with
  fewer than 5 trades in 24 hours are marked **thin**, with the last trade time.
- **Finnhub**, the exchange's own price, once you add a free key in **Studios → Keys**.
  The key travels in a request header, never in an address.

**Outlook, not prediction.** "Bitcoin forecast", "will NVDA go up", or the **Outlook**
tab gives:
- expected ranges for the next hour, day and week (68 % and 95 %), from the last
  week's volatility, with past-error calibration when supported, drawn on dollar charts as a cone;
- trend (12 h against 48 h average), the last day's move and RSI;
- **HAYAT's track record for that market**, tested walk-forward on its own last 30
  days: how often the 68 % range really held, and how often momentum called the next
  day's direction, against a past-only majority-direction baseline.

Historical measurements from 2026-10-11 for BTC, ETH, SOL, NVDA and SPY
(before the non-overlapping evaluation update above):
- the 68 % range held 68–78 % of the time;
- momentum called the next day's direction 47–54 % of the time, no better than
  chance, and the answer says so.

Nobody can reliably predict prices, and HAYAT does not pretend to.

**World events → industries.** "Which markets are affected by today's events", or the
**Events** tab, groups significant events by the industries they usually touch.
Each industry shows its funds or shares and today's move. The rules are written out
with their reasons ([market_events.py](../app/market_events.py)):
- Gulf of Mexico storms → oil, gas and LNG.
- Strong quakes in Taiwan, Japan or Korea (magnitude 6+) → semiconductors and Korea.
- Middle East strikes and shipping attacks → oil, gold and defence.
- War in Europe → defence, gas and European shares.
- Copper-country quakes and unrest (Chile, Peru) → copper miners.
- Ransomware, breaches and outages → cybersecurity.
- Crypto hacks → coins and crypto companies.
- Strong geomagnetic storms → satellites.
- Nuclear news → uranium.
- Floods and drought in farming regions → agriculture.

For one event, `market_event_impact` shows how each market moved since the event
was reported, **next to the S&P 500 over the same time**. A broad fall then doesn't
look like the event's effect. When the markets were closed, or traded thinly since the
event, the answer says the changes are not yet a reaction.

Real data tightened the rules:
- 1,000 routine "known exploited vulnerability" notices no longer count as cyber
  news.
- A magnitude-4 quake in Chile no longer counts as a copper risk.
- A knife attack in Poland is not "war in Europe".
- "Heat" no longer matches "HEATHER Fire".

**Trading stays impossible.** Buying, selling, shorting or investing in stocks is
stopped and explained exactly like crypto, for example "buy 10 apple shares" or "sell
my nvidia stock".

## Scenarios from live data (2026-10-11)

"What are the chances bitcoin goes up this week", "why is NVDA down", "analyze XLE", or the
Trade Center's **Scenarios** tab. HAYAT reads up to two years of daily prices and takes the
price streaming live this second as today's close. It then describes the situation now:
- **Trend:** price against its 20-day average.
- **Volatility:** the last 10 days' swings against the last 120 days'.
- **RSI:** over 14 days.

**The odds.** For every past day in a situation like now, HAYAT counts how often the market was
higher a day later and a week later, next to how often it was higher on all days:
- Each share comes with its sample size and a 95 % interval.
- The verdict says plainly whether the situation changed the odds, or is "within the noise",
  or there are "too few past cases to tell".
- It uses the most specific match with at least 15 past cases, and trend alone otherwise.
- Week-ahead samples are a week apart, so they don't overlap.

On 2026-10-11 Bitcoin's odds were within the noise, as most are, and the window says so.

**The coming week:** weak, middle and strong prices. These are the 10th, 50th and 90th
percentile of past weekly moves in the same volatility state, applied to the live price. A
marker moves along them with each trade.

**What might matter.** Each factor is a reading that leans up or down or adds risk:
- trend, volatility, RSI and the last day's move;
- **world events in its industries** (nearness, not cause);
- **how it moves with the S&P 500 or Bitcoin:** correlation over 90 days, plus that market's
  move today;
- thin trading.

**Explain with HAYAT** hands only these numbers to the local model for a few plain sentences. It
is told never to predict a price, claim a cause or give advice.

## Possibilities by time period (2026-10-12)

"What are the chances bitcoin goes up in a month", "will bitcoin go up next year", "where will
tesla stock be in 6 months", "bitcoin long term outlook", "bitcoin possibilities", or the Trade
Center's **Periods** tab. HAYAT measures every period from an hour to a year on the market's own
past and says how often each outcome happened. It never says where the price will be.

| Periods | Candles | History |
|---|---|---|
| 1 hour, 4 hours | hourly | the last 30 days |
| 1 day, 1 week, 1 month | daily | the last two years |
| 3 months, 6 months, 1 year | weekly | back to 2013 for Bitcoin |

For each period:
- **Chance higher** at the end, with a 95 % interval counted on *separate* periods. Overlapping
  windows are not independent evidence: two years of daily data hold only 24 separate months.
- **Price ranges** from the live price: the middle half (25th–75th percentile of past moves) and
  9 in 10 (5th–95th). Each range is worked out two ways: from all past periods as they were, and
  scaled to today's volatility. HAYAT uses whichever held better in its own test, and names it.
- **Track record.** Each range is tested walk-forward on the market's past, using only data from
  before each test. The middle half should hold about half the time and 9 in 10 about 90 %. A
  range that held less often is called "too narrow", in amber in the table.
- **Possibilities:** how often the move was a big fall, a fall, about flat, a rise or a big rise.
  The steps are sized to the period (for example ±10 % for a Bitcoin month).
- **On the way:** how often the price touched a step up or down during the period, even when it
  ended elsewhere.
- **After a move like the last one:** the chance higher after past periods that followed a rise
  (or a fall) of the same length, with the verdict "within the noise" or "too few past cases to
  tell" when that is the truth.
- **Scheduled inside it:** Fed decisions, jobs reports and earnings (with a Finnhub key) that fall
  within the period.

**Calm or volatile times like now (2026-10-13).** For each period, HAYAT gives the chance higher in
past periods that began as calm (or as volatile) as today, against all periods, with how much wider
or narrower the moves were. Bitcoin on 2026-10-13 was calm, with swings 0.6× usual. Only 5 separate
months began that calm, so the answer is "too few past cases to tell".

**Do the "like now" odds help?** Each past test point uses only earlier data. The odds after a like
move and the plain odds are both scored against what happened (Brier score; lower is better):
- conditional odds that did worse say "ignore them";
- ones that were no better say so.

For Bitcoin's month, over 12 tests, the like-now odds did worse (0.277 against 0.263).

**Explain with HAYAT** (Periods tab) hands only these numbers to the local model for a plain
summary. It is told never to predict a price or give advice.

**Too little history is said, not guessed.** At least 10 separate periods are needed. Kraken's
tokenized stocks began trading in August 2025, so Apple, Nvidia and the funds stop at 1 month:
"too little history: only 4 separate periods of 3 months".

**Compare across markets.** "Compare btc, eth and spy over a year", "compare my watchlist",
"which coin has the best odds this month", or **Compare my watchlist** in the tab. These rank
one period across the markets given, else the watchlist, else a standard set (Bitcoin, Ether,
Solana, S&P 500, Nasdaq, gold, Nvidia, Apple). The answer warns that the intervals usually
overlap, so a few points' difference is noise. Company names alone ("compare apple and
microsoft") stay an ordinary question unless a market word or a period is added.

**Measured on 2026-10-12 (live Kraken data):**

| Market | Period | Higher in | Ranges held (middle / 9 in 10) |
|---|---|---|---|
| Bitcoin | 1 hour | 50.8 % | 50 % / 90 % of 669 tests |
| Bitcoin | 1 day | 49.9 % | 50 % / 89 % of 669 tests |
| Bitcoin | 1 month | 53.3 % (34–71 %) | 50 % / 67 % of 12 tests: too narrow, and it says so |
| Bitcoin | 1 year | 66.9 % (40–86 %, 13 years) | too few tests to check |
| Apple | 1 week | 59.4 % | 50 % / 85 % of 46 tests |

Long periods mirror a market's own past growth: Bitcoin rose over most past years, which need not
repeat, and the answer says so. A thin market's unchanged hours pull "higher" below half without
any fall, so those are marked "thin trading" and never coloured as a lean.

**The Periods tab:**
- **Cone:** one column per period, with the 9-in-10 range (line), the middle half (bar) and the
  middle move (tick), on a square-root scale so an hour and a year fit side by side. The share
  higher sits on top, green or red only when its whole interval clears 50 %.
- **Table:** higher, middle half, 9 in 10 and how often the ranges held.
- **Details:** click a row or a column for the details above.

The side tabs are now named Book, Outlook, Events, Scenarios, Calendar and Periods, so all six
fit the panel.

Code: [market_periods.py](../app/market_periods.py), [Periods tab](../app/gui_markets_periods.py),
tests in [test_market_periods](../tests/test_market_periods.py).

## Scheduled events and rumours (2026-10-12)

**Calendar.** "When is the next fed meeting", "what events could move markets this week", the Trade
Center's **Calendar** tab, or the Command Center's Calendar tile.
- **Fed interest-rate decisions:** read from the Federal Reserve's own FOMC calendar page, which
  lists published dates, past and coming (54 decision days, 2021–2027).
- **US jobs report:** usually the first Friday at 08:30 ET. The Bureau of Labor Statistics refuses
  automated reading of its schedule ("Access Denied" to bots), so HAYAT shows the usual pattern,
  labelled as such, and doesn't get around the refusal.
- **Company earnings** (Apple, Microsoft, Nvidia and other large companies): from Finnhub's
  earnings calendar once you add a free Finnhub key in Studios → Keys. The key goes in a header.

**What these days did before.** HAYAT measures the two-day move (the close before to the close
after) on every past event day, against the same move on all days, from Kraken's daily prices.
Measured on 2026-10-12:

| Event | Market | Moved over two days vs usual |
|---|---|---|
| Fed decisions | gold (GLD) | 1.7× |
| Fed decisions | Bitcoin | 1.18× |
| Fed decisions | S&P 500 | 0.84× (slightly *less* than usual) |
| Jobs reports | S&P 500 | 1.29× |

Bigger past moves mean bigger swings were common, not which way the next one will go. The
Scenarios tab adds a "Scheduled" factor when such an event is within 10 days.

**News and rumours.** "Market news", "crypto news", "news about NVDA", "any rumors about
bitcoin":
- **Sources:** headlines from public feeds (BBC Business, the Guardian, MarketWatch, NPR,
  CoinDesk, Cointelegraph, Decrypt), with robots.txt honoured.
- **Linking:** each headline is linked to the markets it names. Companies and tickers can come
  from the headline or summary; industry words and country or industry funds count only in the
  headline.
- **Rumours:** headlines with rumour wording are marked **UNCONFIRMED**: "reportedly", "sources
  say", "people familiar", "in talks", "considering a sale", "takeover approach". Ordinary
  "could" or "plans to" don't count; they made every forecast a rumour.
- **Moves since:** for each headline, how the named markets moved since it was published,
  next to the S&P 500.
- A rumour can prove wrong; HAYAT never treats one as fact. The Scenarios tab adds an
  "Unconfirmed report" factor when one names the market.

## Signal lab, what moves a market, and 3D views (2026-10-14)

**Signal lab** ("which signals work for bitcoin", "backtest solana", "is NVDA predictable", or the
Trade Center's **Signals** view). Most market "signals" are noise that looks like a pattern, so
HAYAT tests them instead of trusting them (app/market_signals.py):
- **Ten signals:** 5- and 20-day momentum, price against its 50- and 200-day averages, snap-back
  after a big day, RSI extremes, streak reversal, moves on heavy volume, the day of the week
  (learned only from earlier days) and following a leading market (Bitcoin for coins, the S&P 500
  for shares and for Bitcoin).
- **Walked forward on up to two years of daily prices,** using only what was known on each day, for
  the next day and the next week (week tests a week apart, so they never overlap). Each call is
  compared with the market's own up-rate as it was known then, so "always up" on a rising market is
  not an edge. A test proves no call ever reads the future.
- **Corrected for luck.** Trying ten signals over two horizons, one or two look good by chance. A
  signal counts only if it survives a false-discovery test across every try (Benjamini–Hochberg,
  10 %) **and** beat chance in both halves of its history. Verdicts: *beat chance*, *maybe luck*,
  *no edge*, *backwards* (did the opposite; HAYAT does not lean on it either way), *too few*.
- **Combined odds only from proven signals,** shrunk toward the usual odds (60 pretend calls at the
  usual rate) and never more than 15 points from them.
- **Out-of-sample check:** the whole procedure is re-run choosing signals on the earlier two-thirds
  of the history only, then scored on the later third (Brier skill against the usual odds).
- **Measured live on 2026-10-14:** for Bitcoin, Ethereum and Nvidia no signal beat chance after
  allowing for luck. Bitcoin's streak reversal was right 60 % of 70 calls, and Ethereum's day of
  the week 54 % of 396, but both were within what luck gives across all the tries. The answer says
  so: the usual odds are the honest answer.

**What moves it** ("what drives bitcoin", "what moves apple stock", "what is ETH correlated with",
or **Signals → What moves it**). HAYAT lines up the market's daily moves with Bitcoin, Ethereum,
the S&P 500, the Nasdaq-100, gold, chip stocks, energy stocks and the euro against the dollar
(app/market_influences.py):
- correlation over 90 days and a year, and whether the link held or changed;
- its move per 1 % of theirs (beta), and its average move on their worst tenth of days;
- whether a market's move one day told anything about the next day here (a strict bar, since eight
  markets are tried; none passed for Bitcoin, Ethereum or Nvidia);
- **today's move split** by a combined fit over 90 days: how much goes with the wider markets and
  how much is its own (news, flows, anything else). Bitcoin: about 81 % of its daily swings move
  with these markets;
- world events in its industries, scheduled events and unconfirmed reports, from the scenarios.
- **Thinly traded markets are refused, not called "unlinked".** Kraken's chip and energy funds
  (SMH, XLE) were unchanged on 39–53 of 90 days, so their stale prices made Nvidia look barely
  linked to chips. A market unchanged on more than 20 % of recent days is listed as "not measured"
  with the reason.

**3D view** (**3D** above the chart, or D). One scene to turn, move and zoom (app/gui_space3d.py):
- **Markets:** about 18 coins and shares (your watchlist first) placed by return over 1 week, 1
  month or 3 months (left–right), swing (height) and how closely each moves with Bitcoin or the
  S&P 500 (depth). Size is traded value and colour today's move; thin markets are faded. Axes fit
  nine markets in ten, and an outlier (a coin up 33 % in a month) is pinned to the edge and marked ›
  instead of squeezing the rest into a corner.
- **Odds surface:** every period from an hour (front) to a year (back) against every move
  (left–right); the height is the share of past periods that ended at or above that move, from the
  measured 5th–95th percentiles (beyond them the surface fades and says it is not measured). The
  white line is the chance of ending higher.
- **Links:** the market in the centre and the markets that move with it around it: nearer for a
  tighter link, higher for a bigger move per 1 %, green with and red against, dashed when the link
  changed over the year, → when one leads it.
- **Hands and keys:** drag to turn (a flick keeps turning and slows down), Shift+drag or right-drag
  to move, the wheel zooms toward the pointer, hover reads a sphere or a surface patch, click
  selects, double-click opens the market (or resets on empty space); arrows turn, +/− zoom, 1–4 for
  3D, front, top and side views, Home resets, Tab and Shift+Tab step through the points, Enter
  opens, Space spins. Depth cues: perspective, fog on far things, a floor grid, each point's shadow
  and a drop line to the floor, and labels placed nearest first so a cluster never becomes a blot.
  With Windows animations off, a flick stops, views snap and spinning is unavailable, but every
  action still works.

**Fixed while checking at the owner's screen size:**
- The segment buttons (Crypto/Stocks, 1D–1Y, Chart/Heatmap) were styled only once Global
  Intelligence had opened, so a Trade Center opened first showed them as plain light boxes. The
  style now comes with the theme.
- The side tabs were cut to "Bo", "Out", "Eve" at 1536 px. They are compact now, and the side
  pane is a little wider, so all six names show.

## World trade chokepoints

"How is world trade", "shipping chokepoints", or the **World trade** layer in the browser atlas
(Map & live layers). It covers 11 chokepoints: Hormuz, Malacca, Suez, Bab-el-Mandeb, Panama,
the Taiwan Strait, the Bosporus, Gibraltar, the Danish Straits, the Cape of Good Hope and Dover.

For each one:
- what usually passes;
- the events HAYAT's feeds reported within 350 km in the last 72 hours (red: a high-severity
  report, gold: others, green: none);
- the industries and funds it links to, with today's moves.

These are event reports, not ship traffic: there is no keyless live shipping-volume source, and
an event nearby does not mean trade was disrupted.

## Ask in chat

| You say | You get |
|---|---|
| "Bitcoin price", "how much is ETH?", "price of solana in euros", "PEPE price" | Price on Kraken, change since 00:00 UTC, 24 h range and volume, and Coinbase's price as a cross-check |
| "Show me the bitcoin chart", "ETH chart for the last month" | The change, high and low over 1D / 1W / 1M / 3M / 1Y |
| "Bitcoin order book", "recent BTC trades" | Bids and asks with the spread; the latest trades |
| "How is the crypto market?", "biggest crypto gainers today", "crypto losers" | The top markets by volume, by gain or by fall |
| "Bitcoin fees", "ETH gas", "mempool", "web3 stats" | Bitcoin fees, mempool and block height; Ethereum gas, block time and load |
| "Add solana to my watchlist", "show my watchlist" | A local watchlist |
| "Alert me when bitcoin goes above 90000", "tell me if eth drops below 2k", "check my price alerts", "delete alert 2" | Local price alerts |
| "Apple stock price", "nvidia shares", "SPY price", "how are stocks doing" | Tokenized share price (and the exchange price with a Finnhub key) |
| "Bitcoin forecast", "will bitcoin go up", "NVDA prediction" | Expected ranges, trend and HAYAT's measured track record |
| "Which markets are affected by today's events", "how are world events affecting markets" | Industries touched by today's events, with their funds' moves |
| "What are the chances bitcoin goes up this week", "why is NVDA down", "analyze apple stock", "XLE scenarios" | Odds from its own past, live, with what might matter |
| "Chances bitcoin goes up in a month", "will bitcoin go up next year", "where will tesla stock be in 6 months", "bitcoin possibilities", "bitcoin long term outlook" | Every period from 1 hour to 1 year: chance higher, ranges with their track record, possibilities |
| "Compare btc, eth and spy over a year", "compare my watchlist", "which coin has the best odds this month" | One period ranked across markets, with the warning that close shares are noise |
| "How is world trade", "shipping chokepoints", "is shipping disrupted?" | The chokepoints and the events near each |
| "When is the next fed meeting", "economic calendar", "what events could move markets this week" | Scheduled events with countdowns and past reactions |
| "Market news", "crypto news", "news about NVDA", "market rumors", "any rumors about bitcoin" | Headlines naming markets, rumours marked unconfirmed, moves since |
| "Which signals work for bitcoin", "backtest solana", "test the signals for ETH", "is NVDA predictable", "does momentum work for BTC" | The signal lab: ten signals tested on its own past, corrected for luck, combined only when proven, checked on the later third |
| "What drives bitcoin", "what moves apple stock", "what is ETH correlated with", "what does solana move with" | Its links to eight markets, today's move split into "with the markets" and "its own", and events touching it |

Forecasts, scenarios and periods open with one line in plain words before the
numbers (2026-10-24), for example: "💬 In plain words: nobody can know. Over
past 1 month periods BTC ended higher 52.7% of the time, about a coin flip: the
past does not lean either way." It says "coin flip" whenever the interval spans
50%, and an outlook says plainly that it cannot tell the direction.

"What is bitcoin?" and "how does Ethereum work?" are ordinary questions,
answered normally; only price, chart, book, trade and chain questions read the
markets.

## The Trade Center window

Open it from **Studios → Trade Center (crypto, stocks, world events)**.

**Advanced views (2026-10-11):**
- **Live ticker strip.** It shows Bitcoin, Ether, Solana, the S&P 500, Nasdaq,
  Nvidia, Apple, gold and energy. Each streams its own last trade and 24-hour change,
  and flashes green or red as it moves. Click one to open it.
- **Heatmap** (Chart | Heatmap above the chart). Crypto, big tech, the US market and
  every industry fund appear as tiles, sized by trading volume and coloured by
  today's move: full colour at ±3 %, "thin" for quiet markets. The note says how many
  are rising. Hover for the price, click to open.
- **World events on the chart.** A ◆ marks where an event that bears on this market
  (by the World events rules) was reported. Red means high severity. Hovering names
  the event and says "nearness in time, not proof of cause".
- **Truthful live badge.** **LIVE · 135 ms** while trades arrive. A thin tokenized
  share shows **streaming · last trade 15:47 UTC** instead, and a stray resting order
  (an ask of $0) shows "no live bid/ask" rather than a wrong quote.

**Live charts and alerts (2026-10-11):**
- **The current candle moves with each streamed trade.** The chart redraws at most
  once a second, and the legend says "● last candle live".
- **Overlays:** MA 20 (on by default), MA 50, and **vs S&P 500**. The comparison line is
  the S&P 500 fund rescaled to start where this market starts, so the two moves read
  side by side. The legend under the toggles gives each line's value in its own colour.
- **Heatmap ◆.** A tile marked ◆ belongs to an industry with world events. Hovering
  says how many.
- **Alerts window** (the **Alerts** button, or A): every alert, waiting or reached, with
  the time and price it was reached. Delete one, or **Check now**. Double-click opens
  the market.
- **Alerts are checked in the background:** every two minutes while HAYAT runs, and
  every five seconds from the live stream while this window is open. A reached alert
  is reported in the conversation, the terminal and by voice. With no alert waiting,
  nothing is read. It is only a notice: nothing is ever bought or sold.
- **The watchlist mixes coins and shares.** ☆ Watch now works for a stock (it used to
  refuse), and the ★ Watchlist view shows both together.
- **Keys:** Ctrl+F search, 1–5 chart range, H heatmap, D 3D, S signals (each pressed again goes
  back to the chart), W watch, A alerts. They do nothing while you type in the search box.

**Fixed:** "check my price alerts" never reported a reached alert. Reading the price
marked the alert as reached first, so the check that followed found nothing. It now
reports what the price read marked.

**Fixed:** the World events tab read the first 1,000 events of any kind, and road
events filled that, so it showed no industries. It now reads each kind of event its
rules know. Severity is no longer a filter, since the rules require specific wording
(6 real conflict reports rated "low" had been dropped).

- **Markets:** every crypto market on Kraken in USD, EUR, USDT, USDC or GBP
  (about 630 in USD). Search by name or ticker. Views: All, ★ Watchlist,
  Gainers and Losers. Government currencies (EUR, GBP and so on) are left out.
- **Coin:** price, change today (UTC), 24 h range, volume, trade count,
  bid/ask, and the Coinbase cross-check. The candlestick chart has volume bars,
  a last-price line and a hover readout (open, high, low, close, volume) over
  1D to 1Y.
- **Order book** with depth bars and the spread, and **recent trades**.
- **Web3 · network state:** Bitcoin block height, fees (sat/vB) and waiting
  transactions; Ethereum gas (slow/average/fast gwei), block time,
  utilisation and transactions today. Public data only; no wallet is
  connected.
- **★ Watch** and **Price alert…** buttons for the selected coin. A reached
  alert shows in the footer with a sound.

The coin on screen refreshes every 10 seconds, and the market list, chart and
chain every 60. Requests go one at a time from a background thread, and
answers are cached, so the window stays responsive and the public APIs are
not hammered.

## Sources

| Source | Used for | Notes |
|---|---|---|
| Kraken public API | Assets, pairs, tickers, candles, order book, trades | Free, keyless; no robots.txt restriction |
| Coinbase Exchange public API | Asset names, cross-check price, rolling 24 h change | Free, keyless |
| mempool.space | Bitcoin fees, mempool, block height | Free, keyless |
| Blockscout (eth.blockscout.com) | Ethereum gas, block time, activity | robots.txt allows it |
| Kraken WebSocket v2 | Live trades and tickers, crypto and tokenized shares | Public; delay measured per trade |
| Kraken tokenized assets | Stock and fund prices, candles, order books | Keyless; they track, not equal, exchange prices |
| Finnhub | Exchange stock quotes | Free key you add in Studios → Keys |
| Kraken EUR/USD | The euro against the dollar, for "what moves it" | Keyless daily candles |

**Not used, because their robots.txt forbids automated access to the API:**
CoinGecko (`/api/v3`) and Binance (all paths). HAYAT honours these refusals.

## Limits

- "Change today" is since 00:00 UTC, Kraken's daily open; the Coinbase figure
  is a rolling 24 hours. Both are labelled.
- Only coins listed on Kraken appear, about 630 crypto assets, not every
  token in existence.
- Price alerts are checked every two minutes while HAYAT runs, live while the
  Trade Center is open, and on "check my price alerts". They are not checked
  while HAYAT is closed, and they send nothing to anyone.
- "Web 3.0" here means reading public blockchain state. There is no wallet
  connection, no signing and no transactions. The full explorer (addresses,
  transactions, blocks, ENS, Layer 2s, DeFi) is in the
  [Web3 explorer](web3.md).
- "Web 4.0" has no agreed technical definition. HAYAT uses it for the agentic
  web and checks what a website publishes for AI agents
  ([Web3 explorer, agentic web](web3.md#agentic-web-web-40)).

Code: [markets](../app/markets.py), [Trade Center window](../app/gui_markets.py),
[views](../app/gui_markets_views.py), [periods](../app/market_periods.py), [background alerts](../app/market_alerts.py),
tests in [test_markets](../tests/test_markets.py).
