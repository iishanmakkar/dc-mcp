# Muse connector submission -- ready-to-paste answers

Fill the `[BRACKETS]` first. **Deploy the server before you submit** (see README): the form asks for a real product website,
and reviewers will test the connector end to end.

| Form field | Answer |
|---|---|
| **Connector name** | Agentic Data Cleaner  _(short alternative: Data Cleaner)_ |
| **Company or developer** | Ishan Makkar |
| **Product website** | `https://agentic-data-cleaner.onrender.com/` (serves the landing page; `/privacy`, `/terms`, `/support`, `/install`, `/about` are built in) |
| **Example prompts** | see below |
| **Connector icon** | `muse/icon.png` (512x512) or `muse/icon.svg` |
| **Payments** | **"My connector accepts payments"** once paid features are switched on (`BILLING_ENABLED=true`); otherwise **"does not accept payments"** for launch. See `docs/monetization.md` -- confirm Stripe availability for you first. |
| **Your name** | Ishan Makkar |
| **Work email** | ishanmakkar651@gmail.com |
| **Support email or URL** | ishanmakkar651@gmail.com (`https://agentic-data-cleaner.onrender.com/support`) |
| **Your privacy policy** | `https://agentic-data-cleaner.onrender.com/privacy` |
| **Your terms of service** | `https://agentic-data-cleaner.onrender.com/terms` |
| **Anything else?** | see below |

## Example prompts

1. "Clean this customer spreadsheet: fix the whitespace, the N/A placeholders and the date formats, and give me the cleaned file."
2. "Scan this CSV and tell me everything that's wrong with it before you change anything."
3. "Remove the duplicate rows from this export, and show me which ones you'd remove first."
4. "Check this file for personal data like emails and phone numbers and mask them."
5. "The dates in this sheet are day/month/year. Convert them and export the result as an Excel file."

## Anything else? (optional)

> Agentic Data Cleaner is an MCP server (Streamable HTTP at `https://agentic-data-cleaner.onrender.com/mcp`, bearer-key authentication).
> Muse does the reasoning; the connector exposes deterministic tools and never calls a third-party AI model. Safe fixes are
> applied automatically; anything that deletes rows/columns, fills in missing values or masks personal data requires the user's
> explicit approval for that specific change. The user's original file is never modified; working copies and download links are
> deleted after about an hour. Tools carry read-only / destructive / open-world annotations. Test account/key: [PROVIDE ON REQUEST].

## Before you click submit -- checklist

- [ ] Deployed: `fly.toml` (Fly.io) or `render.yaml` (Render) -- both configs are prepared, see README section 3
- [ ] `python tools/preflight.py https://[YOUR-DOMAIN] YOUR_KEY` prints **ALL CHECKS PASSED** (17 checks covering exactly what reviewers will touch)
- [ ] `API_KEYS` set (never run open), `PUBLIC_BASE_URL` and `ALLOWED_HOSTS` set to your real domain
- [ ] `docs/privacy-policy.md` and `docs/terms-of-service.md` filled in (no `[PLACEHOLDER]` left) and reviewed by a lawyer
- [ ] Verified how Muse authenticates to your MCP server (bearer key vs OAuth) against Meta's current developer docs -- the
      developer portal is login-walled from here; the server currently supports a bearer key, swappable in one function
      (`server/app.py::_client_id`)
- [ ] Tried the connector from Muse itself with each example prompt
- [ ] Payments: read Meta's developer terms and Stripe's availability for your country before enabling billing
      (`BILLING_ENABLED=true`)
