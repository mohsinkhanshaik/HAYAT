# Command Center

Open it from **Studios → Command Center (everything live)**, or press **Ctrl+K**
and type "command". It's one screen with everything live, in eight tiles. Each tile opens its
full window when clicked.

| Tile | What it shows | Opens |
|---|---|---|
| Markets · live | Bitcoin, Ether, Solana, the S&P 500, Nvidia and gold, streaming trade by trade with the measured delay. Each has a 24-hour sparkline whose last point moves with the live price. Prices flash green or red as they move. | Trade Center |
| World pulse | Events reported in the last 24 hours by severity and by kind. The most severe come newest first, world reports before road closures. | Global intelligence |
| World trade | A world map of the 11 shipping chokepoints, coloured by the reports nearby. Alerts pulse. Hover a dot for the reports and linked funds. | Browser atlas |
| Odds board | For Bitcoin, Ether and the S&P 500: how often each was higher a week later in situations like now, against usually, with the honest verdict. | Trade Center |
| Chains & DeFi | Bitcoin block, fees, mempool and hashrate; Ethereum and Layer 2 gas and load; DEX volume over 24 hours. | Web3 explorer |
| Calendar | The next scheduled event that moves markets, counting down: Fed decisions from the Fed's own calendar, the jobs report's usual day, and earnings with a Finnhub key. Chips show how much markets moved on past ones against usual days. | Trade Center |
| News & rumours | Business and crypto headlines that name markets, rumours tagged UNCONFIRMED, with the named markets' moves since. | Trade Center |
| Yours | Which accounts are connected, actions waiting for your confirmation, price alerts, watched addresses and email sending. Each line opens its window. | Accounts |

**How it updates:**
- The clock and live prices update several times a second.
- Each tile refreshes on its own schedule: about one to ten minutes for public data, and every
  20 seconds for your own items.
- **F5** refreshes everything.

**Getting the counts right.** World pulse counts every event, using the intelligence store's
own totals, and leaves public cameras out. A single capped list of 1,000 rows used to be all
road traffic and hid everything else.

The Command Center is read-only: nothing here trades, sends or signs.

Code: [gui_command_center](../app/gui_command_center.py), tests in
[test_command_center](../tests/test_command_center.py).
