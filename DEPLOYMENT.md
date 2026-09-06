# Deploying to Render

## What was failing, and why

The build log showed one error, but there were actually **two** problems
stacked behind it.

### Problem 1 — the visible one: a dependency conflict

```
The user requested reportlab==4.2.2
svglib 2.2.0 depends on reportlab>=4.4.3
```

`svglib` (used to draw the SVG logo on PDF invoices) was upgraded to 2.x, and
every 2.x release requires `reportlab>=4.4.3` — but `reportlab` was pinned to
`4.2.2`. Those two packages must move together.

**Fix:** bumped `reportlab` to `4.4.3`. Both PDF paths (customer payout invoice
and buyer invoice) were tested against it and still generate correctly.

> Downgrading `svglib` instead does *not* work: no `svglib` release old enough
> for `reportlab 4.2.2` ships a pre-built wheel any more.

### Problem 2 — the hidden one: Render is using Python 3.14

```
==> Using Python version 3.14.3 (default)
```

Python 3.14 is very new, and several pinned dependencies have **no pre-built
wheel** for it yet:

| Package | Oldest version with a 3.14 wheel | Pinned here |
|---|---|---|
| Pillow | 11.3.0 | 10.4.0 |
| pymongo | 4.15.2 | 4.8.0 |

You can see this starting in the log already — Pillow was downloading a **46 MB
`.tar.gz` source archive** instead of a wheel, meaning it was about to try
compiling from scratch.

So even after fixing the reportlab conflict, the build would have failed again
on Pillow, and then again on pymongo.

**Fix:** added a `.python-version` file pinning **Python 3.12.7** — the version
the application was developed and tested against, and one where every pinned
dependency has a ready-made wheel.

---

## Render service settings

| Setting | Value |
|---|---|
| **Environment** | Python |
| **Build command** | `pip install -r requirements.txt` |
| **Start command** | `gunicorn run:app` |

`run.py` exposes `app` at module level, which is what `run:app` refers to.

---

## Required environment variables

Set these under **Environment → Environment Variables** in the Render dashboard.
Never commit them to the repository.

| Variable | Notes |
|---|---|
| `SECRET_KEY` | Long random string. Signs the session cookie — if it's guessable, sessions can be forged. |
| `MONGO_URI` | Your MongoDB Atlas connection string. |
| `MONGO_DB_NAME` | e.g. `milk_dairy` |
| `FLASK_DEBUG` | Leave unset, or `False`. **Never `True` in production.** |
| `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` / `SMTP_FROM` | Only needed if you want email notifications and invoice emails. |

### MongoDB Atlas: allow Render to connect

The app builds its database indexes at startup, so **if MongoDB is unreachable
the service will fail to boot.** In Atlas:

1. **Network Access → Add IP Address**
2. Render's outbound IPs are not fixed on the free tier, so allow `0.0.0.0/0`
   (open to all IPs) — the connection is still protected by your database
   username and password.
3. Confirm the database user in **Database Access** has read/write permission.

---

## After the first successful deploy

If you already had data in the database from before the accounting module
existed, generate its ledger entries once, from Render's **Shell** tab:

```bash
flask backfill-ledger
```

It's safe to run more than once — re-running replaces entries with identical
figures rather than duplicating them.

---

## Known caveat: uploaded profile photos don't survive a redeploy

Render's filesystem is **ephemeral**. Anything written at runtime — including
profile photos saved to `app/static/uploads/avatars/` — is wiped on every
deploy and on every restart.

The app handles this gracefully (it falls back to the initials avatar), so
nothing breaks. But if you want photos to persist, you'd need either:

- a **Render Persistent Disk** mounted at `app/static/uploads`, or
- an external object store such as Amazon S3 or Cloudinary.

This is a platform characteristic, not a bug in the application.

---

## Upgrading to Python 3.14 later

Nothing here prevents it — you'd just need to lift the pins that lack 3.14
wheels, roughly:

```
pymongo>=4.15.2
Pillow>=11.3.0
```

Re-test PDF generation and profile photo upload afterwards, since both depend
on libraries with compiled components. Until you need something from 3.14,
staying on 3.12 is the lower-risk choice.
