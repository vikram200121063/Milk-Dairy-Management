# Milk Dairy Management — User Stories

_Prepared: 2026-09-27_

Covers 5 personas and every shipped feature of the Milk Dairy Management System, written as user stories with acceptance criteria.

## Overview & Personas

| Persona | Who they are | Access |
| --- | --- | --- |
| Owner | The dairy's proprietor/admin | Full access to every screen |
| Staff | A helper who records day-to-day collection | Customers & milk entries only |
| Customer | A farmer/supplier who delivers milk | Self-service portal, own records only |
| Buyer | A company the dairy sells milk to in bulk | Public payment link, no login |
| Visitor / Guest | Anyone evaluating the app (e.g. a recruiter) | Read-only live demo, no login |

Stories are grouped by feature area (an "epic") below, each numbered `US-#` for reference.

## Account & Access Management

**US-01 — Log in**
As an Owner or Staff member, I want to log in with a username and password, so that I can securely access the admin dashboard.
- Wrong username or wrong password shows the same generic error (doesn't reveal which usernames exist).
- A successful login sends me to the dashboard, or back to whatever page I was trying to reach.

**US-02 — Add an account from the UI**
As an Owner, I want to add a new Owner or Staff account from a screen in the app, so that I never need terminal/CLI access to manage who can log in.
- Profile menu → "Manage Staff" is visible only to Owners.
- The form takes username, password, confirm password, and a role (Owner/Staff).
- A duplicate username is rejected with a clear message; mismatched passwords are rejected before anything is saved.

**US-03 — Reset another account's password**
As an Owner, I want to set a new password for any account, so that someone who forgot theirs can get back in without me ever knowing their old password.
- New password must be at least 6 characters and match its confirmation.

**US-04 — Change a role**
As an Owner, I want to promote a Staff account to Owner or demote an Owner to Staff, so that access matches each person's current job.
- I can't change my own role (prevents accidentally locking myself out).

**US-05 — Remove an account**
As an Owner, I want to delete an account that no longer needs access, so that former staff can't log in anymore.
- I can't delete my own account.
- A confirmation prompt appears before the account is removed.

**US-06 — Restricted Staff access**
As a Staff member, I want to see only the Customers and Milk Entries screens, so that I can do my job without being exposed to revenue or payment data I don't need.
- Buyer, Accounting, Rate Configuration, Payments, and Notifications are hidden from navigation.
- Revenue and expense figures are removed from the dashboard's data before the page even renders — not just hidden by CSS — so there's no way to view them via page source.
- Opening a restricted page directly redirects me to the dashboard with an explanation; a restricted background request returns a plain error instead of breaking the page.

## Customer Management

**US-07 — Add a customer**
As an Owner or Staff member, I want to add a new customer with name, mobile number, address, village, and an optional milk machine code, so that I can start recording their deliveries.
- Mobile number is required and must be a valid 10-digit number.
- A duplicate mobile number is rejected.
- Machine code is optional, but if given, it must be unique across customers.

**US-08 — Find a customer**
As an Owner or Staff member, I want to search by name, mobile number, or customer ID and filter by Active/Inactive status, so that I can quickly find one customer among many.

**US-09 — Edit a customer**
As an Owner or Staff member, I want to update a customer's details, so that I can correct mistakes or reflect changes (new address, new machine code, etc.).

**US-10 — Deactivate / reactivate a customer**
As an Owner or Staff member, I want to mark a customer inactive (and reverse it later), so that former suppliers stop appearing in active workflows without losing their history.

**US-11 — Export customers to CSV**
As an Owner or Staff member, I want to download the customer list — respecting whatever search or status filter is currently applied — as a CSV file, so that I can work with it in Excel or share it with someone else.

## Milk Collection & Machine Import

**US-12 — Record a milk entry**
As an Owner or Staff member, I want to log a customer's delivery (date, shift, milk type, quantity, fat%, snf%) with the rate calculated automatically or entered manually, so that I have an accurate daily collection record.
- Only one entry per customer/date/shift is allowed — a duplicate is blocked outright.
- "Automatic" mode computes the rate from fat%/snf% using the current Rate Configuration.

**US-13 — Bulk import from a milk analyzer machine**
As an Owner or Staff member, I want to upload the CSV/Excel file my milk testing machine exports, so that I don't have to retype every sample by hand.
- A downloadable template shows the expected columns and accepted alternate header names.
- Nothing is saved on upload — a preview screen first shows every row's status (Ready, Duplicate, Unknown Code, or Invalid) so I can review before committing.
- A machine code that doesn't match any customer is flagged, never silently dropped or misfiled.
- The rate is always recalculated on the server from the current Rate Configuration, never trusted from the uploaded file.
- Re-importing the same file skips rows already imported, rather than creating duplicates.
- Every import is logged (who, when, how many added/skipped).

**US-14 — Assign a machine code to a customer**
As an Owner, I want to set a "Milk Machine Code" on a customer's profile, so that imported rows are matched to the right person automatically.

**US-15 — Export milk entries to CSV**
As an Owner or Staff member, I want to download the (filtered) milk entries list as CSV, so that I can review or archive collection records outside the app.

## Rate Configuration & Customer Payments

**US-16 — Configure milk rates**
As an Owner, I want to set the base rate, fat-rate-per-point, and snf-rate-per-point for each milk type, so that automatic rate calculation always reflects current pricing.

**US-17 — Generate a payment cycle**
As an Owner, I want to generate a 10-day payment cycle for a customer that totals their milk quantity and amount and carries forward any previous pending balance, so that I pay them accurately and on schedule.

**US-18 — Record a payment**
As an Owner, I want to record a partial or full payment against a cycle, so that I always know what's been paid and what's still owed.

**US-19 — Branded invoice**
As an Owner, I want a branded, printable PDF invoice for each payment cycle (with a Paid / Partially Paid / Pending badge), so that I can give the customer a proper receipt.

## Customer Self-Service Portal

**US-20 — Register a portal login**
As a Customer, I want to create my own portal login using the mobile number already on file, so that I can check my records without calling the dairy.
- Registration only "claims" an existing customer record by mobile-number match — it never creates a new customer.

**US-21 — View my milk records**
As a Customer, I want to see my daily milk entries and current-cycle totals, so that I can verify what's been recorded is correct.

**US-22 — View my balance and history**
As a Customer, I want to see my pending balance and past payment/invoice history (view, print, or download as PDF), so that I know exactly where my account stands.

**US-23 — Pay online**
As a Customer, I want to settle an outstanding balance online by card, UPI, or netbanking, so that I don't have to visit the dairy in person.

Every portal route only ever shows *my own* records — a customer session is completely separate from an admin session, and every query and every invoice is checked against my own customer ID before it's shown.

## Notifications

**US-24 — Email a single record**
As an Owner or Staff member, I want to email a customer their record for one milk entry, so that they have proof without calling to ask.

**US-25 — Bulk-email a shift's records**
As an Owner or Staff member, I want to email every customer's entry for a given date and shift in one click, so that I can notify everyone at once after a collection round.

**US-26 — Email an invoice**
As an Owner, I want to email a 10-day invoice (single or for a whole cycle) with the PDF attached, so that customers automatically get a payment record.

**US-27 — Notification Log**
As an Owner, I want a log showing exactly what was sent, to whom, and whether it succeeded, so that nothing fails silently — a customer with no email on file is skipped and noted, not treated as an error.

## Buyer Management, Sales & Invoicing

**US-28 — Manage buyers**
As an Owner, I want to record each buyer's company details, contact, GST number, payment terms, and credit limit, so that I have a full picture of who I sell milk to in bulk.

**US-29 — Record a sale**
As an Owner, I want to log a milk sale to a buyer (date, quality, quantity, rate, tax, other charges), so that I know exactly what's owed before it's invoiced.

**US-30 — Generate consolidated invoices**
As an Owner, I want to consolidate every sale to a buyer within a half-month period into one invoice with a single click, so that buyers get one bill per period instead of one per delivery.
- Safe to re-run anytime — it recalculates totals from the underlying sales but never erases a payment or interest charge already applied.

**US-31 — Track buyer payments**
As an Owner, I want to record multiple partial payments against an invoice, each with its own date/method/reference, so that I have a full transaction history, not just a running total.

**US-32 — Automated dunning**
As an Owner, I want overdue invoices to automatically get a reminder email, then start accruing late interest after a grace period, so that I don't have to manually chase every late payer.
- Running the check twice in one day never double-charges interest.

**US-33 — Waive or adjust interest**
As an Owner, I want to waive or partially adjust an interest charge with a note, so that I can handle exceptions (goodwill, a payment already in transit) without losing the audit trail.

**US-34 — Pay via a shared link**
As a Buyer, I want to open a secure payment link (no account or login needed) and pay my outstanding invoice online, so that I can settle my account without calling the dairy or mailing a cheque.
- The link is cryptographically signed so it can't be edited to view or pay someone else's invoice, and it expires after 30 days.

**US-35 — Export invoices to CSV**
As an Owner, I want to download the (filtered) buyer invoices list as CSV, so that I can report on it outside the app.

## Expenses & Accounting Dashboard

**US-36 — Record expenses & other income**
As an Owner, I want to log business expenses (fuel, salary, electricity, maintenance, packaging, other) and other income, so that my financial picture is complete.

**US-37 — Profit & Loss / Cash Flow dashboard**
As an Owner, I want a P&L (accrual) and Cash Flow (cash-basis) view for today, this week, this month, or a custom range, so that I can see how the business is actually doing.

**US-38 — Today's snapshot**
As an Owner, I want an as-of-today snapshot of buyer receivables, customer payables, overdue invoices, and outstanding interest, so that I know my current exposure at a glance.

**US-39 — Financial settings**
As an Owner, I want to configure default payment terms, grace period, annual interest rate, and default tax %, so that the dunning and accounting logic matches my actual business policy.

## Online Payments & Webhook

**US-40 — Checkout options**
As a Customer or Buyer, I want to pay by card, UPI, or netbanking through a standard checkout, so that I have flexible ways to pay.
- The amount charged is always computed on the server from the actual record due, never taken from the browser.
- A "successful" payment is verified against the provider's cryptographic signature before anything is marked paid.

**US-41 — Graceful fallback**
As an Owner, I want the app to work exactly as before if I never set up online payments, so that the app is never broken by a missing integration — every "Pay Now" button simply doesn't appear, and manual payment recording keeps working.

**US-42 — Reliable payment confirmation** *(technical/system story)*
As the system, I want a server-to-server webhook to confirm a captured payment even if the payer's browser never returns to the app, so that a real charge is never lost just because a tab was closed or a connection dropped.
- The webhook's signature is verified before it's trusted; an invalid or missing signature is rejected.
- The same payment can never be credited twice — whichever of the webhook or the browser's own confirmation call arrives first credits it, and the other becomes a harmless no-op, no matter which order they arrive in or whether either is retried.

## Guest/Demo Mode & AI Features

**US-43 — One-click live demo**
As a Visitor (e.g. a recruiter), I want to view a live demo of the real, running dairy with one click and no signup, so that I can evaluate the product with zero friction.

**US-44 — Read-only enforcement**
As an Owner, I want every write action blocked while someone is in demo mode, so that the real dairy's live data can never be altered by someone just looking around.
- A guest attempting to add, edit, or delete anything is redirected with a message; nothing is saved.

**US-45 — AI business assistant**
As an Owner, I want a chat widget that answers plain-language questions about my own data ("Which buyers are overdue?", "How's revenue this month?"), so that I don't have to dig through multiple screens for a quick answer.

**US-46 — AI-drafted reminders**
As an Owner, I want dunning reminder emails drafted by AI and tuned to each buyer's payment history, so that my collection emails don't all read like the same form letter.
- If AI isn't configured, it silently falls back to the original wording — a reminder always goes out either way.

**US-47 — AI P&L summary**
As an Owner, I want a 3–5 sentence plain-English summary of my Profit & Loss for any period, so that I can understand my numbers without reading raw figures.

**US-48 — Receipt photo auto-fill**
As an Owner, I want to photograph a receipt and have the expense form pre-fill amount, date, description, and category, so that entering expenses is fast.
- I always review and submit the form myself — nothing is saved automatically.

**US-49 — AI is fully optional**
As an Owner, I want every AI feature to degrade gracefully with no API key configured, so that the app is completely usable for free, with no AI subscription required.
