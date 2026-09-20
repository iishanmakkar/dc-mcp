# Privacy Policy -- Agentic Data Cleaner

_Last updated: 2026-09-20. This page describes how the software behaves; it is not legal advice._
 
**Who we are.** Agentic Data Cleaner ("the service") is operated by Ishan Makkar, India. Contact: ishanmakkar651@gmail.com.

## What the service does with your data
You (or the assistant you use, such as Muse) send us a data file -- for example a CSV or Excel sheet -- so we can find and fix quality problems in it.

* **Your file is processed in memory** on our server to produce a cleaned copy. We do not modify your original file.
* **Retention:** working copies and download links are deleted automatically after **60 minutes**, or immediately when you (or the assistant) ask us to delete them.
* **We do not read, sell, share, or use your data for advertising, profiling, or to train any machine-learning model.**
* Files may contain personal data (names, emails, phone numbers). The service can flag such columns and, only if you approve, mask them. We do not otherwise inspect or extract personal data.
* **Optional AI model:** by default no data is sent to any third-party AI model. If the operator turns this on, only the column structure is sent, with cell values redacted, and this policy will say so before it happens.

## What we log
We keep operational logs: a hashed client identifier, the type of operation, row and column **counts**, plan tier, timestamps and error codes. **Logs never contain file contents.** Logs are kept for 30 days for security, abuse prevention, billing reconciliation and debugging.

## Who processes data on our behalf
* Hosting provider: Render (region chosen at deploy, shown on the Render dashboard).
* Payment processing (if you pay): handled by Meta's checkout and Stripe; we do not receive your card details.

## Security
Transport is encrypted (HTTPS). Uploaded data is isolated per client and reachable only with an unguessable identifier. Files are size-limited and parsed with restricted formats (no executable formats such as pickle). Links to internal networks are blocked when fetching a file from a URL. No system is perfectly secure: do not upload data you are not permitted to process.

## Your choices and rights
You can delete a dataset at any time through the assistant. Depending on where you live you may have the right to access, correct or delete personal data we hold about you (mainly account/usage logs). Email ishanmakkar651@gmail.com.

## Children
The service is not directed to anyone under 18.

## International transfers
Data is processed in the hosting region (Render, region chosen at deploy). By using the service you understand your data may be processed there.

## Changes
We will update this page and change the date above when the policy changes.
