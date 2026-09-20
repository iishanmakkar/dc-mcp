# Agentic Data Cleaner

Find and fix the problems hiding in your spreadsheets and data files -- from inside any AI assistant
(Muse, Claude, ChatGPT, Cursor, VS Code, or any MCP client).

Connect your assistant to `/mcp` on this server (Streamable HTTP, `Authorization: Bearer <key>`) -- see `/mcp.json` for a ready-made client config.

* **Finds** duplicates, placeholder nulls (`N/A`, `?`, `-`), numbers and dates stored as text, spelling variants, encoding damage, outliers and personal data.
* **Fixes the safe things automatically** and asks you before anything risky (deleting rows, filling in values, masking personal data).
* **Never touches your original file.** Working copies are deleted after about an hour.
* **Paid plan:** larger files, an audit report of every change, and a replayable Python script for next month's file.

[Privacy](/privacy) | [Terms](/terms) | [Support](/support)
