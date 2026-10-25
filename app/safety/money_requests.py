"""Recognise a request for HAYAT to move money, and answer it in full, every time.

The authority kernel already refuses payments and purchases in code. But a
request like "buy AirPods on Amazon for me" or "pay my electricity bill"
used to fall through to ordinary chat, so what Mohsin heard back depended on
the model's wording -- and "send 50 dollars to Ali on PayPal" went to the
action planner. The owner asked (2026-10-10) that money always stops, is
explained in detail, and hides nothing. This check runs in the intent router,
before any model or tool, so the answer is fixed text built from his words:
what he asked, what HAYAT understood (amount, recipient, service, item),
what happened, why, what was recorded, and what HAYAT can do next.

Only requests to act are matched. Questions about money -- "how do I buy a
house", "explain how PayPal works", "what should I buy" -- are ordinary
questions and are answered normally.
"""
from __future__ import annotations

import re

from app.safety.authority import MONEY_REASON

#: Coins by name and ticker (2026-10-10, with the read-only Trade Center): "send 1 eth to Ali"
#: and "sell my bitcoin" were not stopped, because the list knew only "crypto", "bitcoin" and "usdt".
_COINS = (r"\b(?:btc|eth|ether|ethereum|sol|solana|xrp|ripple|doge|dogecoin|ada|cardano|bnb|usdc|ltc|litecoin|"
          r"trx|tron|shib|ton|toncoin|xlm|stellar|xmr|monero|bch|dot|polkadot|avax|link|chainlink|pepe|"
          r"coins?|nfts?|stablecoins?|altcoins?)\b")
_MONEY_WORDS = (r"(?:\$\s?\d|\d+(?:\.\d+)?\s?(?:dollars?|usd|rupees?|rs\.?|inr|euros?|pounds?|dirhams?|riyals?)\b|"
                r"\bmoney\b|\bfunds\b|\bpaypal\b|\bvenmo\b|\bzelle\b|\bcash ?app\b|\bupi\b|\bgpay\b|"
                r"\bgoogle pay\b|\bwire\b|\bbank\b|\bcrypto\b|\bbitcoin\b|\busdt\b|"
                # Stocks too (2026-10-11, with stock prices in the Trade Center): "sell my nvidia stock" was not stopped.
                r"\bstocks?\b|\bshares?\b|\bequit(?:y|ies)\b|\betfs?\b|\boptions\b|\bfutures\b|\bxstocks?\b|" + _COINS + r")")

# Imperative requests: the verb leads (after "please"/"go ahead and"), or
# follows "I want you to" phrasing.
_LEAD = r"^(?:please\s+|go ahead and\s+|now\s+|just\s+)*(?:i (?:want|need) you to\s+)?"
_BUY = re.compile(_LEAD + r"(?:buy|purchase|check ?out|place (?:an |the |my )?order|order)\b(?!\s+(?:by|of|these|them|the files?|alphabetically|or)\b)(?!\s*,)",
                  re.IGNORECASE)
_PAY = re.compile(_LEAD + r"(?:make (?:a |the )?payments?\b|pay\b)(?!\s+attention\b)(?!\s+(?:a\s+)?visit\b)", re.IGNORECASE)
# "withdraw"/"deposit" stay out: they are the owner's own accounts, and the
# earnings wallet only records them (wallet_withdraw never moves money).
_TRANSFER = re.compile(_LEAD + r"(?:send|transfer|wire|tip|donate)\b", re.IGNORECASE)
# Selling, trading, swapping or staking crypto also moves money (only with a money word,
# so "trade ideas" or "sell me on this plan" stay ordinary requests).
_TRADE = re.compile(_LEAD + r"(?:sell|trade|swap|stake|short|long|margin|leverage|invest|put|withdraw\s+my\s+crypto|cash\s+out)\b",
                    re.IGNORECASE)

_SYMBOLS = {"$": "USD", "£": "GBP", "€": "EUR", "₹": "INR"}
_UNITS = {"dollar": "USD", "usd": "USD", "rupee": "INR", "rs": "INR", "inr": "INR", "euro": "EUR", "eur": "EUR",
          "pound": "GBP", "gbp": "GBP", "dirham": "AED", "aed": "AED", "riyal": "SAR", "sar": "SAR"}
_AMOUNT = re.compile(
    r"(?P<symbol>[$£€₹])\s?(?P<a>\d[\d,]*(?:\.\d+)?)"
    r"|(?P<b>\d[\d,]*(?:\.\d+)?)\s?(?P<unit>dollars?|usd|rupees?|rs\b\.?|inr|euros?|eur|pounds?|gbp|dirhams?|aed|riyals?|sar)\b"
    r"|\bpayment of\s+(?P<c>\d[\d,]*(?:\.\d+)?)",
    re.IGNORECASE)
_SERVICES = ("paypal", "venmo", "zelle", "cash app", "cashapp", "upi", "gpay", "google pay", "apple pay", "bank transfer",
             "amazon", "ebay", "flipkart", "noon", "walmart", "aliexpress", "temu", "etsy", "dominos", "domino's",
             "uber eats", "doordash", "talabat", "swiggy", "zomato", "steam", "crypto", "bitcoin", "binance")
_SERVICE = re.compile(r"\b(?:on|via|using|through|with|from|at|by)\s+(" + "|".join(re.escape(name) for name in _SERVICES) + r")\b",
                      re.IGNORECASE)
_STOP = r"(?=\s+(?:on|via|using|through|with|from|at|by|for|today|now|tonight|tomorrow|please|asap)\b|[.,!?]|$)"
_RECIPIENT = re.compile(r"\bto\s+((?:my\s+)?[\w'.-]+(?:\s+(?!on\b|via\b|using\b|through\b|for\b|with\b|from\b|at\b|by\b)[\w'.-]+)?)" + _STOP,
                        re.IGNORECASE)
_ITEM = re.compile(_LEAD + r"(?:buy|purchase|order|place (?:an |the |my )?order for|pay(?: for)?)\s+(?:me\s+|us\s+)?"
                   r"(?:(?:a|an|the|some|my|our)\s+)?(.+?)" + _STOP, re.IGNORECASE)


def money_request(text: str) -> bool:
    """True when the text asks HAYAT itself to buy, pay or move money."""
    value = " ".join(str(text or "").split()).strip(" .!")
    if not value or value.endswith("?") and not _PAY.match(value) and not _BUY.match(value):
        return False
    if _BUY.match(value):
        # "order" alone is too broad ("order these by date"); it needs a shop.
        if re.match(_LEAD + r"order\b", value, re.IGNORECASE) and not re.search(
                r"(?i)\b(?:online|from|on (?:amazon|ebay|flipkart|noon|walmart|aliexpress|temu)|delivery|food|pizza)\b", value):
            return False
        return True
    if _PAY.match(value):
        return True
    return bool((_TRANSFER.match(value) or _TRADE.match(value)) and re.search(_MONEY_WORDS, value, re.IGNORECASE))


def describe(text: str) -> dict[str, str]:
    """What the request says, read literally. A missing detail stays "not stated"; nothing is guessed."""
    value = " ".join(str(text or "").split()).strip(" .!?")
    kind = ("purchase" if _BUY.match(value) else "payment" if _PAY.match(value)
            else "money transfer" if _TRANSFER.match(value) else "trade" if _TRADE.match(value) else "money movement")
    details = {"action": kind, "amount": "not stated", "recipient": "not stated",
               "service": "not stated", "item": "not stated"}
    amount = _AMOUNT.search(value)
    if amount:
        number = amount["a"] or amount["b"] or amount["c"]
        currency = (_SYMBOLS.get(amount["symbol"] or "") or
                    _UNITS.get(re.sub(r"[^a-z]", "", (amount["unit"] or "").lower()).rstrip("s"), ""))
        details["amount"] = f"{number} {currency}".strip() if currency else f"{number} (currency not stated)"
    recipient = _RECIPIENT.search(value)
    if recipient:
        details["recipient"] = recipient.group(1)
    service = _SERVICE.search(value)
    if service:
        details["service"] = service.group(1)
    if kind in {"purchase", "payment"}:
        item = _ITEM.match(value)
        if item and not re.fullmatch(r"(?i)(?:it|this|that|them|now|\d[\d,.]*)", item.group(1).strip()):
            words = item.group(1).strip()
            if not _AMOUNT.fullmatch(words) and not re.match(r"(?i)of\s+\d", words):
                details["item"] = words
        if details["item"] == "not stated":
            # "pay $120 to Comcast for the internet bill"
            bill = re.search(r"\bfor\s+(?!me\b|us\b|him\b|her\b|them\b)(?:(?:the|my|our|a|an)\s+)?(.+?)" + _STOP,
                             value, re.IGNORECASE)
            if bill:
                details["item"] = bill.group(1)
    return details


def money_reply(text: str, parsed_from: str | None = None) -> str:
    """The complete, fixed answer to a request to move money. Nothing is left out."""
    details = describe(parsed_from or text)
    quoted = " ".join(str(text or "").split())[:300]
    return "\n".join([
        "I stopped this. Nothing was paid, bought, sent or transferred.",
        "",
        f'What you asked: "{quoted}"',
        "What I understood (read from your words; nothing guessed):",
        f"- Action: {details['action']}",
        f"- Amount: {details['amount']}",
        f"- Recipient or shop: {details['recipient']}",
        f"- Service: {details['service']}",
        f"- Item or bill: {details['item']}",
        "",
        "What I did: nothing outside this chat. No website was opened, no form was filled, and no card, "
        "bank account, wallet or password was read or used. No model or tool was asked to do it, and it is "
        "not added to background research.",
        "What is recorded: this request and this reply, in your local chat history on this computer, like "
        "any other message. Nothing about it was sent anywhere.",
        "",
        "Why: " + MONEY_REASON,
        "Where the rule lives: app/safety/authority.py (tier 4, PROHIBITED). Settings and "
        "config/permissions.yaml cannot loosen it, and it cannot be granted in advance.",
        "",
        "What I can do next, if you say so:",
        "1. Find and compare the options, prices, fees and delivery times.",
        "2. Fill in the order, delivery or billing details -- everything except the payment.",
        "3. Open the payment page and stop there, with the amount and recipient shown, so you check them "
        "and pay yourself.",
    ])
