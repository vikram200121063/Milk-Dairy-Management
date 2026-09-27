# Milk Dairy Management — Functional & Technical Design Document

Sep 27, 2026 · Prepared by @Support

## Purpose & How to Use This Document

This document exists so you can explain both **what** each feature does and **why** it's built the way it's built — the two things a technical interview actually probes for.

- Every module below is split into three parts: **Functional** (what the user experiences), **Technical** (the code-level design decisions and data flow), and **Interview Angle** (the questions most likely to come up, with a tight spoken answer already drafted for you).
- Re-read one module right before discussing that part of the project; read the whole document once end-to-end before the interview itself.
- The module order follows how data actually flows through the app: accounts → customers → milk collection → rate/payment cycles → the customer portal → notifications → buyers/sales/invoicing → accounting → online payments → supporting features (guest mode, AI, testing, deployment).
- Companion documents: `README.md` (setup/run instructions), `USER_STORIES.md` (feature-by-persona acceptance criteria) — this document sits between them, connecting *what it should do* to *how it actually does it*.

## System Architecture & Tech Stack

The app is a single Flask service built with the application-factory pattern, MongoDB for storage, and server-rendered Jinja2 templates — every layer chosen to be free, simple, and easy to defend in an interview.

*(Diagram: request flow — browser → Flask Blueprints (RBAC-gated) → MongoDB via PyMongo, with Razorpay connected for checkout + webhook. See the live diagram in the companion Claude Docs version of this document.)*

**Functional:** one deployable web app serves Owner/Staff admin screens, the Customer self-service portal, public Buyer payment links, and the Guest demo — all from the same codebase and the same database.

**Technical:**

- `create_app()` in `app/__init__.py` builds the Flask app, registers every Blueprint (auth, customers, milk, users, buyers, pay, profile, …), and attaches `app.db` (a PyMongo `Database` handle) so any route reaches Mongo through `current_app.db`.
- Each feature area is its own Blueprint under `app/routes/` — independently readable and independently testable.
- `config.py` reads every secret/setting from environment variables once, at first import (this detail mattered later — see Testing Strategy).
- Auth is a signed session cookie (Flask's built-in session), not JWT/OAuth — the right level of complexity for a single-server admin app with no external API consumers.
- Deployment: gunicorn as the WSGI server, Render as the host — both free-tier compatible, matching the project's zero-cost constraint.

**Interview Angle:**

- *"Why Flask over Django?"* — Flask's minimalism fit a small, single-database app; there was no need for Django's ORM/ORM-migrations/admin overhead.
- *"How does one codebase serve four different kinds of users?"* — role and session flags branch behavior at the route level (RBAC decorators) and the template level (conditional nav), not via separate deployments or separate codebases.
- *"Why session cookies instead of JWT?"* — there's no separate API client to authenticate; the browser and the server are the only two parties, so a signed server-side session is simpler and just as secure.

## Data Model Reference

Every collection lives in one MongoDB database; there is no separate database per role or per demo mode.

| Collection | Key fields | Unique index(es) | Purpose |
| --- | --- | --- | --- |
| `users` | username, password\_hash, role, created\_at | username | Owner/Staff login accounts |
| `customers` | customer\_id, name, mobile, address, village, machine\_code, active | mobile; machine\_code (sparse) | Milk-supplying farmers |
| `milk_entries` | customer\_id, date, shift, milk\_type, quantity, fat%, snf%, rate, amount | (customer\_id, date, shift) | Daily collection records |
| `payment_cycles` | customer\_id, cycle\_start, cycle\_end, total\_qty, total\_amount, carry\_forward, paid, status | — | 10-day customer payment cycles |
| `buyers` | buyer\_id, company\_name, gst\_number, payment\_terms, credit\_limit | — | Bulk milk buyers |
| `sales` | buyer\_id, date, quality, quantity, rate, tax, other\_charges | — | Individual sales to a buyer |
| `buyer_invoices` | buyer\_id, billing\_period, total, interest\_accrued, status | (buyer\_id, billing\_period) | Consolidated half-month invoices |
| `buyer_payments` | invoice\_id, amount, date, method, reference | — | Payment history against an invoice |
| `expenses` | date, category, amount, description | — | Business expenses & other income |
| `processed_payment_events` | `_id` = Razorpay payment\_id | `_id` (native Mongo) | Idempotency guard shared by webhook + browser confirm |
| `notification_log` | recipient, type, status, sent\_at | — | Email send audit trail |

**Interview Angle:** *"How do you enforce data integrity without a schema?"* — MongoDB has no column-level schema, so integrity is enforced two ways: application-level validation on every write, and unique indexes on the fields where a duplicate would corrupt business logic (a customer's mobile number, one milk entry per customer/date/shift, one invoice per buyer/billing-period). The `processed_payment_events` collection is the clearest example — its whole job is a unique index, nothing else.

## Module 1 — Authentication, RBAC & Manage Staff

Any Owner can add, edit or remove any admin account from a screen in the app — nobody needs terminal access after the very first account exists.

**Functional:** login with username/password; two admin roles (Owner sees everything, Staff sees only Customers & Milk Entries); a "Manage Staff" screen (visible only to Owners) to add accounts, reset passwords, promote/demote roles, and delete accounts.

**Technical:**

- `session["role"]` holds the signed-in role; `session.get("role", "Owner")` defaults missing/legacy accounts to Owner, so accounts created before roles existed keep full access instead of silently losing it.
- `owner_required` / `owner_required_json` decorators (`app/utils/decorators.py`) gate every Owner-only route, returning a redirect for page routes and a plain JSON error for AJAX/API routes.
- Passwords are hashed with Werkzeug's `generate_password_hash` / `check_password_hash` — never stored or logged in plaintext.
- `app/routes/users.py` implements add/list/reset-password/change-role/delete, each requiring the actor to already be Owner.
- Two self-targeting guards — *you can't change your own role* and *you can't delete your own account* — are the only protection needed: because the decorator already requires the actor to be an Owner, and self-actions are blocked separately, your own login can never be locked out through this screen. An earlier version also carried a live "don't demote/delete the last remaining Owner" count-check; writing the test suite proved that branch could never fire (the target is always a *different* account from the actor, so at least two Owners exist whenever it would matter) — it was removed as dead code rather than shipped for the sake of looking cautious.

**Interview Angle:**

- *"Walk me through what stops a Staff account from seeing revenue data."* — the decorator blocks the route entirely for a background request, and for a page route, the *data itself* is stripped from what's fetched from Mongo before the template renders — not hidden with CSS — so there's nothing to find in page source either.
- *"How did you find and justify removing that dead-code guard?"* — by writing the test for it first and being unable to construct a scenario where it fired; that's a concrete example of test-driven design catching an unreachable branch before it shipped with a misleading comment.

## Module 2 — Customer Management

**Functional:** add, search, edit and deactivate/reactivate customers (name, mobile, address, village, optional machine code); export the current (filtered) list to CSV.

**Technical:**

- Unique indexes on `mobile` and on `machine_code` (sparse, since it's optional) reject duplicates at the database layer, not just in a form check — so a race between two near-simultaneous submissions still can't create two records for the same phone number.
- Search combines a text match (name/mobile/customer\_id) with a status filter (Active/Inactive) into one Mongo query; "deactivate" is a soft flag (`active: false`), never a delete, so history is preserved.
- CSV export re-runs the exact same query object that built the on-screen list, so the file always matches whatever the user is currently looking at — there's no separate "export everything" code path to fall out of sync with the UI filters.

**Interview Angle:**

- *"Why deactivate instead of delete?"* — a customer's milk entries and payment history reference their customer\_id; deleting the customer would either orphan that history or force a cascading delete that destroys financial records. A soft-delete flag keeps the data intact and reversible.
- *"How do you guarantee the CSV matches the screen?"* — by building the query once and using it for both the rendered table and the export, instead of duplicating filter logic in two places.

## Module 3 — Milk Collection & Machine Import

**Functional:** log a single delivery manually (rate auto-computed from fat%/snf%, or entered manually); bulk-import a CSV/Excel file exported by a milk analyzer machine, with a preview screen before anything is saved.

**Technical:**

- A single entry is guarded by a unique index on `(customer_id, date, shift)` — a duplicate is rejected outright rather than silently overwritten.
- Bulk import is a two-step flow: upload → preview → commit. The preview classifies every row as Ready, Duplicate, Unknown Code, or Invalid, and nothing touches the database until the user explicitly commits.
- An unknown machine code (one that doesn't match any customer) is *flagged in the preview*, never silently dropped or misfiled to the wrong customer.
- The rate is always recalculated on the server from the current Rate Configuration — the app never trusts a rate value from the uploaded file, since that file could be stale or edited.
- Re-importing the same file is safe: rows that already exist (by the same duplicate check as manual entry) are skipped, not re-inserted, so retrying an import after a partial failure can't double-count anyone's milk.
- Every import run is logged (who ran it, when, how many rows were added vs. skipped) for audit purposes.

**Interview Angle:**

- *"Why not trust the rate in the uploaded file?"* — the file comes from hardware the app doesn't control; trusting it would let a miscalibrated machine or an edited export silently change what a customer is owed. Recomputing server-side from one source of truth (Rate Configuration) keeps that calculation consistent everywhere in the app.
- *"What happens if I upload the same file twice?"* — nothing bad: the same duplicate check used for manual entries applies row-by-row, so a re-import just skips what's already there.

## Module 4 — Rate Configuration & Customer Payment Cycles

**Functional:** set base rate + fat-rate + snf-rate per milk type; generate a 10-day payment cycle per customer that totals their milk and carries forward any previous pending balance; record partial/full payments; produce a branded, printable PDF invoice per cycle.

**Technical:**

- Rate formula: `amount = base_rate + (fat% × fat_rate) + (snf% × snf_rate)`, applied per-litre at entry time and stored on the milk entry — so a later change to the rate configuration never rewrites history.
- Cycle generation aggregates all milk entries in the date window for that customer, sums quantity and amount, and adds the *previous cycle's unpaid balance* as a carry-forward line — read once from the prior cycle's stored balance, never recomputed by re-summing every past cycle (which would double-count anything already paid).
- Payments are recorded against a specific cycle and update its paid/pending status (Paid / Partially Paid / Pending), which the invoice badge reflects directly.
- PDF generation happens server-side from the cycle's stored data, so the printed invoice always matches what's in the database, not a client-side snapshot.

**Interview Angle:**

- *"How do you avoid double-counting a carried-forward balance?"* — by storing each cycle's own pending balance once it closes and reading only the immediately prior cycle's value forward, instead of re-deriving it from the entire payment history every time.
- *"What if the rate changes mid-cycle?"* — it doesn't retroactively affect anything: the rate is baked into each milk entry the moment it's recorded, so a rate change only affects entries created after that point.

## Module 5 — Customer Self-Service Portal

**Functional:** a customer registers a portal login using the mobile number already on file, then views their own milk entries, cycle totals, balance and payment/invoice history, and can pay outstanding balances online.

**Technical:**

- Registration only *claims* an existing customer record by matching the mobile number — it never creates a new customer document, so the admin-side customer list stays the single source of truth for who's a supplier.
- A customer's portal session is completely separate from an admin session (different session flags), and every single query in the portal routes is scoped by the logged-in customer's own `customer_id` before it ever reaches the template — there's no route that takes a customer\_id from the URL/form without checking it against the session.
- Invoices and PDFs generated in the portal are the same generation code used on the admin side, filtered to that one customer.

**Interview Angle:**

- *"How do you stop one customer from viewing another customer's data by guessing an ID?"* — every portal query is built with the session's own customer\_id baked in as a filter, so even a crafted URL/request with someone else's ID returns nothing — the authorization check isn't a separate step, it's part of how the query itself is constructed.
- *"Why claim an existing record instead of letting customers sign up freely?"* — to guarantee the portal can never be used to inject a fake supplier into the milk-collection records; a login can only ever attach to milk history that an Owner/Staff already entered.

## Module 6 — Notifications

**Functional:** email a single milk-entry record, bulk-email an entire shift's records in one click, email an invoice (with PDF attached), and review a log of exactly what was sent, to whom, and whether it succeeded.

**Technical:**

- All email sends funnel through one notification helper so the success/failure logging logic exists in exactly one place, not duplicated per feature.
- A customer with no email on file is *skipped and explicitly noted* in the log — it is not treated as a failed send, because it isn't an error, it's an expected data gap.
- Bulk sends iterate per-customer and log each attempt independently, so one bad address in a batch of 50 doesn't hide whether the other 49 went through.

**Interview Angle:**

- *"How do you know a bulk send actually worked?"* — the notification log records a row per recipient per attempt, not just an aggregate "sent 50 emails" message, so any silent failure is visible and attributable to a specific customer.
- *"Why distinguish 'skipped' from 'failed'?"* — conflating the two would make a completely normal situation (a customer who's never given their email) look like a bug every time a report runs, and would bury real delivery failures in noise.

## Module 7 — Buyer Management, Sales, Invoicing & Dunning

**Functional:** manage buyer profiles (GST, terms, credit limit); log individual sales; consolidate a buyer's sales for a half-month period into one invoice with a click; record multiple partial payments per invoice; automatically email overdue reminders and accrue late interest after a grace period; waive/adjust interest with a note; let a buyer pay via a signed public link with no login.

**Technical:**

- Invoice consolidation is *idempotent by design*: re-running it for the same buyer/period recalculates the total from the underlying sales but never erases a payment or an interest charge already applied to that invoice — it updates the invoice document's total field, it doesn't recreate the invoice from scratch.
- The dunning job (overdue check + interest accrual) is safe to run more than once on the same day: interest is only accrued once the invoice crosses into a new overdue period, tracked on the invoice itself, so a second run that day sees "already accrued for this period" and does nothing — the same idempotency principle as the payment webhook (Module 9), applied to a scheduled job instead of a payment callback.
- Interest waive/adjust writes an audit note alongside the change rather than just editing the number, preserving *why* an exception was made.
- Payment links are cryptographically signed (HMAC) tokens carrying the invoice reference and an expiry (30 days) — the link can't be edited to point at a different invoice because the signature would no longer match, and an expired token is rejected before any invoice lookup happens.

**Interview Angle:**

- *"How do you stop the dunning job from double-charging interest if it's accidentally run twice?"* — the accrual state lives on the invoice, keyed to the overdue period it already charged for; the job checks that state before charging again, so it's idempotent the same way the payment webhook is — this is a pattern that shows up twice in the codebase and is worth naming explicitly if asked.
- *"Why sign the payment link instead of just using the invoice's database ID in the URL?"* — a raw ID in a URL is guessable/enumerable; a buyer could try adjacent IDs and view or pay someone else's invoice. Signing the token means the server can verify the link wasn't tampered with, and the 30-day expiry limits how long a leaked link stays exploitable.

## Module 8 — Expenses & Accounting Dashboard

**Functional:** log business expenses and other income; view Profit & Loss (accrual basis) and Cash Flow (cash basis) for today/this week/this month/a custom range; see today's snapshot of buyer receivables, customer payables, overdue invoices and outstanding interest; configure default payment terms, grace period, interest rate and tax %.

**Technical:**

- Both P&L and Cash Flow are computed from the *same* underlying `sales`, `buyer_payments`, `payment_cycles` and `expenses` data — they're two different aggregation queries over one dataset, not two separately maintained ledgers that could drift apart.
- P&L (accrual) counts revenue when a sale/invoice is *raised*, regardless of whether it's been paid; Cash Flow counts money only when it actually *moves* (a payment is recorded). The difference is entirely in which date field the aggregation groups by — invoice date vs. payment date.
- The receivables/payables snapshot is a live aggregation run as-of "now" each time the dashboard loads, not a nightly batch job, so it's always current.

**Interview Angle:**

- *"Explain the difference between your P&L and Cash Flow views in your own words"* — expect this verbatim; the answer is: P&L shows what the business earned and spent regardless of when cash actually changed hands (an invoice raised today counts today even if the buyer pays next month); Cash Flow shows only money that's actually moved, which is what tells you if you can pay this week's bills. Same data, two different date fields to group by.
- *"Why not maintain a separate accounting ledger table?"* — a separate ledger risks getting out of sync with the sales/payments it's supposed to summarize; deriving both views from the same source data on demand guarantees they can never disagree with the underlying records.

## Module 9 — Online Payments & Webhook (Razorpay)

This is the single most interview-worthy module in the app — it's the one place where a real financial transaction meets an unreliable network, and the design decisions here are worth being able to defend in depth.

**Functional:** customers and buyers pay by card/UPI/netbanking through standard Razorpay checkout; if Razorpay isn't configured at all, every "Pay Now" button simply doesn't appear and manual payment recording keeps working exactly as before — the app is never broken by a missing integration.

**Technical:**

- The amount charged is always computed on the server from the actual record due (invoice/cycle balance) — the browser only ever *displays* an amount, it never supplies one that gets trusted.
- Two independent paths can confirm the same payment: (1) the browser returns from checkout and calls a verify endpoint with Razorpay's signed response, and (2) Razorpay's server calls a webhook (`/pay/webhook`) directly, regardless of what the browser does. Both paths verify a cryptographic signature before trusting anything — an invalid or missing signature is rejected outright.
- The webhook exists as a *backstop*: if the customer's browser crashes, loses connection, or the tab is closed right after paying, the browser-return path never fires — but the webhook still will, because it's Razorpay's server calling this server directly, independent of the customer's device.

*(Diagram: both the browser return callback and the server-to-server webhook call claim_payment_event(), which inserts the payment_id into a unique-indexed collection — the first call is credited, any repeat becomes a harmless no-op. See the live diagram in the companion Claude Docs version of this document.)*

- Both paths funnel through one function, `claim_payment_event()`, which tries to insert the Razorpay `payment_id` as the `_id` of a document in `processed_payment_events`. Mongo's `_id` is natively unique, so whichever call arrives first succeeds and proceeds to credit the invoice/payment; whichever arrives second (or a retried duplicate of either) hits a `DuplicateKeyError` and becomes a harmless no-op — no matter which of the two paths wins the race, or how many times either is retried.

**Interview Angle:**

- *"Why do you need a webhook if the browser already confirms the payment?"* — because the browser can fail to come back for reasons that have nothing to do with whether the payment actually succeeded (closed tab, dead connection, crashed app). The money already moved at Razorpay; without a server-to-server backstop, that charge would be lost from the app's records with no way to notice.
- *"How do you prevent double-crediting when both paths fire for the same payment?"* — by using the payment provider's own payment ID as a database primary key. This turns "has this payment already been processed?" into a question MongoDB itself answers atomically, instead of a check-then-insert race condition the application code would have to get right.
- *"What if the webhook signature is invalid?"* — rejected before any business logic runs; an attacker who knows the webhook URL still can't fabricate a fake "payment succeeded" call without the shared secret.
- *"How did you test this without a real Razorpay account?"* — covered in the Testing Strategy module below; short answer: a test double that performs the real HMAC-SHA256 signature math, so a tampered signature genuinely fails verification in tests, not just in production.

## Module 10 — Guest/Demo Mode

**Functional:** a one-click "View Live Demo" login with no signup; every write action is blocked with a redirect and a clear message, so a visitor exploring the real, running app can never alter real data.

**Technical:**

- Guest mode is a `before_request` guard (`block_guest_writes`) checked on every request against a session flag, not a separate demo database — a guest reads the *real* `current_app.db` (via RBAC's default-to-Owner behavior, so they can see everything), but any state-changing request short-circuits before it reaches the route handler.
- The `pay` blueprint is explicitly exempted from this guard, because a real payment flow has its own independent safety (server-computed amounts, signature verification) and blocking it outright would make that part of the demo untestable; a bad token there simply 404s rather than hitting the generic guest-write redirect.
- This was rewritten mid-project to drop an earlier design that used a *separate demo database* — that duplicated data and could drift out of sync with the real one; a request guard against the real data is simpler and can't go stale.

**Interview Angle:**

- *"Why guard requests instead of duplicating the database for demo users?"* — a duplicated demo database is a second copy of the truth that needs its own seeding, refreshing and maintenance, and visitors exploring it aren't actually seeing the real app. A request-level write guard against the live data shows a genuinely live product with zero risk to it, and there's nothing to keep in sync.
- *"How do you make sure a new write route doesn't accidentally forget the guest check?"* — the guard runs at `before_request` for the whole app, not per-route, so a new route is protected by default; a blueprint has to be *explicitly* exempted (like `pay`) rather than explicitly opted in, which is the safer default direction for a security control.

## Module 11 — AI-Assisted Features

**Functional:** a chat assistant that answers plain-language questions about the Owner's own data; AI-drafted dunning reminder emails tuned to a buyer's payment history; a 3–5 sentence plain-English P&L summary for any period; a receipt-photo auto-fill for the expense form.

**Technical:**

- Every AI feature has a hard-coded fallback path for when no API key is configured: dunning reminders fall back to the original template wording, the P&L summary section simply doesn't render, the receipt auto-fill button doesn't appear — the app is 100% usable for free, with zero AI subscription required, matching the project's overall zero-cost constraint.
- AI output is always a *draft the Owner reviews and submits themselves* — a drafted reminder still goes through the normal send flow, a receipt auto-fill only pre-populates form fields the Owner can edit before submitting; nothing from an AI call is auto-saved to the database.
- The chat assistant is scoped to the Owner's own data queries (dashboard-style read access), not a general-purpose chatbot with open-ended database access.

**Interview Angle:**

- *"How do you design a feature to 'degrade gracefully' when a paid dependency isn't configured?"* — by treating the AI-configured state as just another branch checked once (is a key present?), with a deterministic, already-correct fallback on the other branch — never a code path that assumes AI is always available and crashes or blocks the user when it isn't.
- *"Why review-before-save instead of having AI act autonomously?"* — these are financial/business communications; an AI-drafted email or a mis-read receipt amount has real consequences if sent or saved unreviewed. Keeping a human in the loop for anything that writes data is a deliberate trust boundary, not a missing feature.

## Testing Strategy & Quality Assurance

**Functional:** a 41-test `pytest` suite covering RBAC, CSV export, milk import, the payment webhook, guest mode, and the Manage Staff screen — runnable by anyone who clones the repo with `pip install -r requirements.txt -r requirements-dev.txt && pytest`.

**Technical:**

- `tests/fake_mongo.py`: an in-memory MongoDB double, shipped in the repo (no real database, no network, no cost). Each `MongoClient()` call creates a fresh, independent in-memory store, so function-scoped `app`/`db` fixtures automatically get full test isolation — no test can see another test's data, with no manual reset code needed. It enforces `_id` uniqueness and any explicitly registered `create_index(unique=True)` field, matching real MongoDB's constraint behavior closely enough that the same duplicate-key logic the app relies on in production (see Module 9's idempotency guard) is genuinely exercised in tests.
- `tests/_fakes/razorpay/`: a Razorpay SDK double that performs *real* HMAC-SHA256 signature math, rather than rubber-stamping every signature as valid. A test that submits a tampered webhook payload genuinely fails signature verification — the same code path a real attacker would hit, not a mocked-out shortcut.
- `conftest.py` installs the fake Mongo module into `sys.modules` *before* the first `from app import create_app` anywhere in the process, since Python caches imports; doing this after the first import would leave the real `pymongo` already bound in some module and silently break isolation.
- All of this is free by construction — satisfying the project's standing zero-cost rule for the test suite itself, not just the production app.

**Interview Angle:**

- *"Why write your own Mongo fake instead of using `mongomock`?"* — no external dependency to install, keeping the test suite genuinely zero-setup; the fake only needs to support the small surface of PyMongo operations this app actually uses, which is straightforward to implement and easy to reason about compared to a full third-party emulation layer.
- *"What makes a fake Razorpay SDK trustworthy as a test double?"* — it does the real cryptographic signature computation instead of always returning "valid." That's the difference between a test that proves the *plumbing* is wired up and a test that proves the *security property* actually holds — a tampered signature has to genuinely fail, or the test isn't testing anything real.
- *"How do you get test isolation without resetting a database between tests?"* — by making each fixture's fake client its own fresh in-memory store rather than sharing one store and cleaning it up; there's nothing to leak between tests because nothing is shared.

## Deployment, Configuration & Security

**Deployment:** gunicorn as the WSGI server, hosted on Render's free tier; `flask create-admin <username>` is the one unavoidable CLI step (bootstrapping the very first Owner account, since nobody is logged in yet to use the Manage Staff screen with) — every account after that is created from the UI.

**Configuration (environment variables):** `SECRET_KEY`, `MONGO_URI`, `MONGO_DB_NAME`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` — all read once at `config.py` import time, never committed to the repo.

**Security practices used throughout:**

- Passwords hashed (Werkzeug), never stored or logged in plaintext.
- Every payment amount computed server-side; every payment confirmation signature-verified.
- Every Owner-only route gated by a decorator, not by hiding a nav link.
- Buyer payment links are signed and time-boxed rather than raw database IDs.
- Guest/demo sessions can read real data but are blocked from any write at the request level.

**Interview Wrap-Up — if you're asked only one question about this whole project:**

*"What was the most interesting design problem you solved?"* — lead with the payment idempotency guard (Module 9): two independent, unreliable paths (a browser that might never come back, a webhook that might arrive twice) both need to safely confirm the same real-world event exactly once, and the whole solution is one unique-index insert. It's small, it's testable without any real payment account, and it generalizes — the same pattern was reused for the dunning job's interest accrual (Module 7). That one idea — *let the database's own uniqueness constraint do the hard concurrency work instead of application-level locking* — is the strongest single thread running through this codebase, and it's worth being able to explain from first principles, not just point at the code.
