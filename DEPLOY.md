# Deploying the Population Data Explorer

The built site is **~3.2 GB across 4,120 pages**. GitHub Pages caps a published
site at 1 GB, so this goes to S3 behind CloudFront — the same pattern, and the
same AWS account, as `gridded-eif`.

Two buckets, two distributions, one account:

| | bucket | serves |
|---|---|---|
| Data | `eil-gridded-eif-data` | Parquet, catalog, crosswalks |
| Site | `eil-population-explorer` | this site's HTML |

Everything below is one-time setup. After it, publishing is:

```bash
explorer build --all && explorer-publish
```

---

## 0. Before you start

- Use **`us-east-1`**, matching gridded-eif.
- You need permissions for S3, CloudFront, IAM and Budgets.
- **When you're done, send me the values in [§7](#7-what-to-send-me).** Skip §4: the site is cited at its github.io address, which forwards to CloudFront (see the end of this file).

---

## 1. S3 bucket

Console → **S3 → Create bucket**

| Setting | Value |
|---|---|
| Name | `eil-population-explorer` |
| Region | `us-east-1` |
| Block *all* public access | ✅ **Leave ON** |
| Bucket versioning | **Disable** |
| Encryption | SSE-S3 (default) |

Two deliberate differences from the data bucket:

**Block Public Access stays on**, same as gridded-eif. The site is public but
served *through CloudFront*, never from the bucket URL.

**Versioning is off**, unlike the data bucket. There, versioning is cheap
insurance on a few GB of irreplaceable output. Here, every rebuild rewrites
essentially all 4,120 pages, so versioning would accumulate a few GB per
deploy forever, for pages that are regenerable from source in four minutes.

---

## 2. CloudFront function — **do this before the distribution**

This is the step that is easy to miss and breaks every page if you do.

Every page lives at a directory URL (`/county/51003/`). S3-as-REST-origin has
no directory index, and CloudFront's "Default Root Object" applies only to
`/`. Without this function, only the front page works and all 4,120 place
pages 404.

Console → **CloudFront → Functions → Create function**

- Name: `explorer-index-rewrite`
- Runtime: **cloudfront-js-2.0**
- Paste the contents of [`deploy/index-rewrite.js`](deploy/index-rewrite.js)
- **Publish** it (saving alone does not make it usable)

---

## 3. CloudFront distribution

Console → **CloudFront → Create distribution**

| Setting | Value |
|---|---|
| Origin domain | `eil-population-explorer.s3.us-east-1.amazonaws.com` |
| Origin access | **Origin access control settings (recommended)** → Create new OAC |
| Viewer protocol policy | **Redirect HTTP to HTTPS** |
| Allowed methods | GET, HEAD |
| **Compress objects automatically** | ✅ **Yes** |
| Cache policy | **CachingOptimized** |
| Default root object | `index.html` |

Under the default behaviour, **Function associations**:

| Event type | Function |
|---|---|
| Viewer request | `explorer-index-rewrite` |

Leave "Legacy cache settings" alone — CachingOptimized already honours the
`Cache-Control` headers the publish step sets.

**Compression is not optional here.** The site is 3.2 GB raw and about 0.45 GB
gzipped; CloudFront's brotli does better still. It compresses objects between
1 KB and 10 MB, and the largest page is ~4.4 MB, so every page qualifies.

After creating it, CloudFront shows a **bucket policy** to copy. Do that:
S3 → `eil-population-explorer` → Permissions → Bucket policy → paste → Save.

---

## 4. Custom domain (optional, but decide now)

URLs must not break, so choose before anyone links to it.

- **Without a domain**, the site lives at `https://dxxxxxxxxxxxxx.cloudfront.net`
  — stable, but not memorable, and tied to this distribution forever.
- **With one**, e.g. `explorer.environmental-inequality-lab.org`: request a
  certificate in **ACM in `us-east-1`** (CloudFront only reads certificates
  from that region), add it to the distribution as an Alternate domain name,
  then point a CNAME at the distribution.

My recommendation is the custom domain. A cited URL outlives the
infrastructure behind it, and a CloudFront hostname cannot be moved to a
different distribution later.

---

## 5. GitHub Actions role (OIDC, no stored keys)

If the repo already has the `gridded-eif` OIDC provider, reuse it — one
provider per account.

IAM → **Roles → Create role → Web identity**

- Provider: `token.actions.githubusercontent.com`
- Audience: `sts.amazonaws.com`
- Condition: repo `Environmental-Inequality-Lab/population-explorer` (the code replaces the old explorer in that repo)

Attach an inline policy:

```json
{
  "Version": "2012-10-17",
  "Statement": [
    { "Effect": "Allow",
      "Action": ["s3:ListBucket"],
      "Resource": "arn:aws:s3:::eil-population-explorer" },
    { "Effect": "Allow",
      "Action": ["s3:PutObject", "s3:DeleteObject"],
      "Resource": "arn:aws:s3:::eil-population-explorer/*" },
    { "Effect": "Allow",
      "Action": ["cloudfront:CreateInvalidation"],
      "Resource": "*" }
  ]
}
```

Name it `explorer-site-publisher`.

---

## 6. Budget alarm

Billing → **Budgets → Create budget** → Cost budget, monthly, **$10**, alert at
80%.

Expected cost is a rounding error: 3.2 GB of S3 Standard is about **$0.07 a
month**, and CloudFront egress at $0.085/GB means 10,000 page views is about
**$0.09**. The alarm exists to catch a mistake — a runaway loop re-uploading,
or someone hotlinking — not because the steady state is expensive.

---

## 7. What to send me

1. Bucket name, if not `eil-population-explorer`
2. CloudFront **distribution ID** (`E1XXXXXXXXXXXX`)
3. CloudFront **domain** (`dxxxxxxxxxxxxx.cloudfront.net`) or the custom domain
4. IAM **role ARN** (`arn:aws:iam::123456789012:role/explorer-site-publisher`)

Then either:

- Add 2 and 4 as GitHub repo secrets `EXPLORER_DISTRIBUTION_ID` and
  `AWS_ROLE_ARN`, and the workflow deploys on push; or
- Give me credentials locally (`aws configure` / SSO) and I'll run the first
  publish by hand so we can look at it before automating.

---

## Publishing by hand

```bash
export EXPLORER_BUCKET=eil-population-explorer
export EXPLORER_DISTRIBUTION_ID=E1XXXXXXXXXXXX

explorer build --all
explorer-publish --dry-run     # says what would change, touches nothing
explorer-publish
```

`publish` compares each local file's MD5 against the object's ETag in S3 and
uploads only what changed, so a data refresh that moves 200 pages uploads 200
pages, not 3.2 GB. It also deletes objects that are no longer built — a place
that disappears upstream must stop resolving rather than serve stale figures.

## Checks worth running after the first deploy

```bash
curl -sI https://YOUR-DOMAIN/county/51003/ | head -20
```

- `HTTP/2 200` — the index rewrite works (a 404 means §2 is missing or unpublished)
- `content-type: text/html; charset=utf-8`
- `content-encoding: br` or `gzip` — compression is on (§3)
- `cache-control: public, max-age=300, must-revalidate`

```bash
curl -sI https://YOUR-DOMAIN/county/51003 | head -3
```

- `HTTP/2 301` with `location: /county/51003/` — the canonical-slash redirect

---

## The cited address: GitHub Pages redirects

The site is cited as **https://environmental-inequality-lab.github.io/population-explorer/**
(`render.SITE_URL`), which is where the old explorer lived. GitHub Pages cannot
serve a 3 GB site or sit in front of CloudFront, so that address publishes only
a redirect site: a forwarding page at every path of the real site, plus a
`404.html` that forwards any other path unchanged. It is about 2.5 MB. The old
explorer is deleted outright; its addresses are not mapped.

After the first CloudFront deploy, once the distribution's domain is known:

```bash
explorer build --all
python tools/build_redirects.py --target https://dxxxxxxxxxxxxx.cloudfront.net
```

Then publish the output folder (printed by the script) as the `gh-pages`
branch of `Environmental-Inequality-Lab/population-explorer`, and set the
repo's Pages source to that branch. This replaces the old explorer.

**Changing hosts later** (a custom domain, say) is the same command with the
new `--target`, pushed again. Nothing on the main site changes, and every
address anyone has cited keeps working. Rerun it whenever places are added or
removed, so new pages have a forwarding page too (the 404 fallback catches
them in the meantime).
