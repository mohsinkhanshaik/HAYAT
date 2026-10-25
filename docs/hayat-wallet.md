# HAYAT Wallet: decisions, pots, bills and rules

HAYAT works with the money in your wallet instead of only reading it. It decides what should happen next and
asks you; you approve or decline. Open **Studios → HAYAT Wallet (decisions, pots, bills)**, the **Decisions &
pots** button in **Business → Wallet**, or ask in chat.

The wallet is the money recorded in your business books (earned, plus money you added, minus expenses and money
taken out). It sits in your own bank, PayPal or store accounts; HAYAT keeps the record.

## What HAYAT decides

Press **Think now**, or say "what should I do with my money?". HAYAT reads the books, pots, bills, rules and
portfolio, and proposes, each with its reasons and the numbers:

| Proposal | When | If you approve |
|---|---|---|
| **Top up a pot for a bill** | a bill is due within 10 days and its pot is short | the shortfall moves into the pot |
| **Pay a bill** | a bill is due within 10 days | the money is held; you pay it in your bank or app; HAYAT opens the biller's page |
| **Put money to work in your pots** | money is unallocated and pots are under their targets | it is shared out: emergency fund first, then tax, bills, savings, investing, spending; bills due in 30 days without a pot stay covered |
| **Start an emergency fund** | you have spending but no emergency pot | a pot with a target of three months of spending |
| **Keep money aside for tax** | you have income but no tax pot | a Tax pot and a rule proposing 25% of each new payment (check your own rate) |
| **Set aside a rule's share** | a new payment arrives and its rule isn't automatic | the share moves into the pot |
| **Rebalance your portfolio** | your portfolio drifts more than 5 points from your target mix | you make the trades at your broker; HAYAT never trades |
| **Warning** | a bill can't be covered, or the next 30 days' bills exceed your free money | marked as seen |

A declined proposal is not proposed again; new numbers (a new payment, a new due date) make a new one. A
proposal whose reason has gone (the bill was paid, the money moved) is dropped by itself. A top-up for a bill and
the share-out never fill the same pot twice.

**Can I afford it?** "Can I afford a 300 dollar laptop?", "I want to spend 20 on a domain from marketing", or the
**Can I afford…?** button. HAYAT checks the pot or your unallocated money, the bills due in 30 days, your
emergency fund and your income, and says **Yes, it fits**, **Better to wait** or **Not enough**, with the reasons.
The request then waits for your approval like any other decision.

## Your permission, in code

- **Only you approve.** In the window, or by saying "approve decision 4", "decline decision 4" or "decision 4 is
  paid ref TX-123" at this computer. No tool can approve, so neither HAYAT's model nor a web page can approve its
  own proposal, and a remote device is refused.
- **Inside the wallet, an approval acts at once:** money moves between pots, a rule's share is set aside, a pot is
  created.
- **Outside the wallet, you pay.** An approved bill or purchase holds the money ("held for payments") until you
  say it is paid. Then the books record it, as a **business expense** when it is for the business, otherwise as
  **money taken out** for a personal payment, and the pot goes down by the same amount. A bill then moves to its
  next due date. **Release** gives held money back.
- **Standing permission you choose:** a rule marked *automatically* ("set aside 25% of income for tax
  automatically") is applied by HAYAT to each new payment without asking. It only moves money between your pots,
  and only for payments recorded after you made the rule.
- HAYAT never pays, buys, enters card details or moves money between real accounts. Payments and purchases by
  HAYAT itself stay refused in `config/permissions.yaml` (`financial_transaction` and `purchase` are DENY). If a
  pot lives in a separate bank account, move the money there too.

## Pots, bills and rules

| Say | Does |
|---|---|
| "create a pot called Tax with target 2000" | a pot (purpose guessed from the name: tax, emergency, bills, savings…) |
| "move 300 to tax", "move 200 from savings to tax", "put 500 into emergency fund" | moves money inside the wallet |
| "add bill rent 1200 monthly due 2026-11-01" | a bill (once, weekly, monthly or yearly); the window also takes the paying pot, the biller's https page and whether it's a business bill |
| "set aside 25% of income for tax" (add "automatically" to skip asking) | a rule |
| "show my pots", "show my bills" | the desk |
| "what should I do with my money?" | HAYAT thinks and lists its decisions |

"Move 500 to my bank account" is not a pot move: money leaving the wallet is a real transfer, which HAYAT doesn't
make.

The **Rules & targets** tab also sets your portfolio's target mix (cash, stocks and crypto adding up to 100%),
which the rebalance check uses with [the portfolio](portfolio.md).

## Where it is kept

In the business books, `data/memory/business.db`: tables `pots`, `pot_moves`, `pot_rules`, `rule_applied`,
`bills`, `bill_payments`, `decisions` and `desk_settings`. Every decision keeps its reasons, what approving meant,
when you decided and the outcome, so the **History** tab shows what HAYAT proposed and what you chose.
