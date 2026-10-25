# Checking an email address

Added 17 October 2026. Code: [app/email_check.py](../app/email_check.py); tool `email_address_check`.

## Why it exists

On 5 October a conversation reply claimed that an "IP trace" put the owner's address
`mohsinkhanshaik@hamuzair.com` in Charlotte, North Carolina. No tool had run, and the real Windows
location fix was in another state. Background learning then saved "find current location of
<address>" as a research skill. A web search can only guess at that question, because an email
address carries no location: mail is delivered to the domain's mail provider, wherever its servers
are. HAYAT does not trace who holds an address.

That skill (`data/skills/candidates/research_find_location_mohsinkhanshaik_hamuzair_com.yaml`) was
kept, not deleted, and now runs this check instead of a web search. It is still a reviewable
candidate (`trusted: false`). The request itself is routed straight to the check, so it works
without promotion.

## What it checks

| Part | How | Why it matters |
|---|---|---|
| Mail provider | The domain's MX records from public DNS, matched to Google Workspace, Microsoft 365, Zoho, GoDaddy, Titan and others | Says who actually receives the mail |
| SPF | The domain's `v=spf1` TXT record; two records are an error | Without it, mail you send is more likely to land in spam |
| DMARC | `_dmarc.<domain>` and its policy (`none`, `quarantine`, `reject`) | `p=none` only monitors; forged mail is still delivered |
| DKIM | Fifteen common selectors (`google`, `selector1`, `zmail` for Zoho, `fm1`, …) | A signature receivers can check; a selector HAYAT did not try is possible, and the reply says so |
| Published | The domain's home page, `/contact` and `/contact-us`, read with HAYAT's public fetcher (robots.txt honoured), looking for the address written plainly or as `name [at] domain` | A printed address is collected by spam harvesters |
| Where you are | Only for your own address: the Windows location service, through the normal `device_location` tool | The answer to the question that was really asked |

An address counts as yours when the request says so ("check **my** email address …", "where is
**my** email …") or when it is the sending account set up in Business → Email setup. For anyone
else's address, the check covers only the domain's public records and pages. It never reads your
location and never attributes a location to the address.

The lookup uses the `public_api_get` permission, so privacy mode blocks it like any other network read.

## Asking for it

```text
Check my email address mohsinkhanshaik@hamuzair.com
Where is my email mohsinkhanshaik@hamuzair.com
Find current location of mohsinkhanshaik@hamuzair.com
Check the mail setup of sales@example.com
Is my email me@example.com set up correctly?
```

"Send an email to …", "reply to …", "save … as Sam's email" and "email … about the location of the
meeting" keep going to the messaging and contact tools. Only the words just before the address decide.

## The owner's domain, 17 October 2026

A live check of `hamuzair.com` found:
- **Mail provider:** Zoho Mail (`mx.zoho.in`, `mx2.zoho.in`, `mx3.zoho.in`).
- **SPF:** `v=spf1 include:zoho.in ~all`.
- **DKIM:** published under Zoho's `zmail` selector. The first version of the check didn't try `zmail` and wrongly advised turning DKIM on; the selector list now includes it.
- **DMARC:** `p=none`, with reports to `support@hamuzair.com`.
- **Website:** the address is not printed on the home or contact page.

The one suggestion: once the DMARC reports show your real mail passing, move to `p=quarantine`.

Tests: [tests/test_email_check.py](../tests/test_email_check.py) (stand-ins for DNS, pages and location;
the learned question answered end to end without inventing a place).
