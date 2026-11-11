# Deploying DocuMind: website on Vercel, backend on AWS Lightsail

This is a step-by-step guide for putting a working copy of DocuMind online for a client or a portfolio:

```
 visitor ──HTTPS──▶ Vercel (the Next.js website)
                       │  /api/*  is proxied to your server
                       ▼
                 AWS Lightsail server (Docker Compose, HTTPS by Caddy)
                 caddy ─▶ frontend ─▶ backend ─▶ postgres · redis · qdrant
                                      worker (reads documents, builds the search index)
```

Why two places? Vercel runs websites, not long-running servers. DocuMind's backend needs a worker, three
datastores and local AI models (about 4 GB of RAM), so it runs on a small server. The Lightsail server also
serves the complete app on its own address, which is a handy fallback.

**Everything here was prepared and tested piece by piece on a Windows PC** (scripts, the Vercel build, the
account and model safeguards). It has **not** yet been run on a real Lightsail server, and the Docker setup has
not run in CI yet, so expect to fix small things on the first deploy. If something fails, the logs (section 9)
tell you what.

## 1. What it costs and what you need

| | |
|---|---|
| Lightsail server | **$24 per month** for the 4 GB / 2 vCPU / 80 GB plan ([pricing](https://aws.amazon.com/lightsail/pricing/)), the tested default. **512 MB ($5) cannot work.** **1 GB ($7) and 2 GB ($12) work in lean mode** (`deploy.sh HOST --lean`, section 5): a smaller search model and no reranker, about 870 MB in total. Lean mode ran on a Windows PC with those memory caps, not yet on a real 1 GB server; the first Docker build may be slow or need the swap file. AWS bills by the hour up to the monthly price, so a 3-day demo costs a few dollars. |
| Static IP | Included while attached to the instance. **Release it when you delete the instance**: an unattached one is billed. |
| Vercel | Free Hobby plan. It is limited to non-commercial, personal use ([terms](https://vercel.com/docs/plans/hobby)): fine for a portfolio, borderline for paid client work. |
| Host name | A free `duckdns.org` name, or any domain you own. |
| AI usage | Your OpenAI / Google keys. Costs depend on use; set a monthly limit at each provider (section 8). |
| On your PC | PowerShell and `scp`/`tar` (built into Windows 10/11), Node.js for the Vercel CLI. |

## 2. Create the Lightsail server

1. Lightsail console → **Create instance**. Pick a region near you or your client.
2. Platform **Linux/Unix**, blueprint **OS Only → Ubuntu 24.04 LTS**.
3. Plan: the **$24** one (4 GB). Use the normal dual-stack plan, not "IPv6 only".
4. Name it `documind` and create it.
5. **Networking → Create static IP** and attach it to the instance. Note the address (called `SERVER-IP` below).
6. On the instance's **Networking** tab, under **IPv4 Firewall**, add **HTTP (80)** and **HTTPS (443)**. Port 80 is
   needed to get the HTTPS certificate. Keep SSH (22), ideally limited to your own IP.
7. **Account → SSH keys**: download the default key for your region (a `.pem` file).

## 3. Give the server a name

Caddy gets a free HTTPS certificate automatically, but only for a real host name that points at the server.

1. Sign in at [duckdns.org](https://www.duckdns.org) and create a subdomain such as `documind-api`.
2. Set its **current ip** to `SERVER-IP`.
3. Check from your PC: `nslookup documind-api.duckdns.org` should print `SERVER-IP`.

No sign-up wanted? Use `<IP with dashes>.sslip.io`, e.g. `13-206-205-159.sslip.io` for `13.206.205.159`: it resolves to that IP and Caddy can get a real certificate for it (this is what the tested deployment used).

The host name is then `documind-api.duckdns.org` (`HOST` below). Use your own domain instead if you have one:
create an `A` record pointing at `SERVER-IP`.

## 4. Put the code on the server

On your PC, in the project folder:

```powershell
powershell -ExecutionPolicy Bypass -File infrastructure\lightsail\make-bundle.ps1
```

This creates `documind-bundle.tgz` (about 0.5 MB). It contains your working files, including uncommitted
changes, but no `.env`, no secrets and no `node_modules`.

Windows refuses keys that other users can read. Fix the key's permissions once:

```powershell
$key = "$HOME\Downloads\LightsailDefaultKey-ap-south-1.pem"     # your key file
icacls $key /inheritance:r /grant:r "${env:USERNAME}:R"
scp -i $key documind-bundle.tgz ubuntu@SERVER-IP:~/
```

## 5. Install and start everything

Connect (Lightsail's orange **Connect using SSH** button works, or `ssh -i $key ubuntu@SERVER-IP`), then:

```bash
mkdir -p documind && tar -xzf documind-bundle.tgz -C documind && cd documind
sudo bash infrastructure/lightsail/bootstrap-host.sh      # one time: Docker, 4 GB swap, log limits
exit                                                      # log in again so the docker group applies
```

Reconnect and deploy:

```bash
cd documind
bash infrastructure/lightsail/deploy.sh HOST --smoke          # 4 GB plan
bash infrastructure/lightsail/deploy.sh HOST --smoke --lean   # 1-2 GB plan (remembered for later updates)
```

The script:

1. Creates `.env` with new random secrets and **demo-safe defaults**: sign-up closed (`REGISTRATION_ENABLED=false`)
   and only cheap models selectable (`CHAT_MODEL_IDS=gpt-5-nano,gpt-5-mini,gemini-3.5-flash-lite`).
2. Asks for your OpenAI and/or Google key (typed input is hidden) and stores them in `.env` (readable only by you).
3. Builds and starts the stack. The first build takes **5 to 10 minutes**.
4. Waits until `https://HOST/login` answers, which also means the certificate was issued.
5. Offers to **create your admin account** (the only way in, as sign-up is closed).
6. With `--smoke`, uploads a test document and checks search. The first run downloads the AI models (about
   1.5 GB), so allow several more minutes.

Then open `https://HOST`, sign in and try it: upload a document, ask a question. **This address is already a
complete, working DocuMind.**

## 6. Put the Vercel website in front

You only need this if you want the Vercel address. The fastest way needs no GitHub:

```powershell
cd frontend
npx vercel login
npx vercel link                            # create a project, e.g. "documind"
npx vercel env add API_URL production      # value: https://HOST   (no trailing slash)
npx vercel --prod
```

Or, from the Vercel dashboard: **Add New → Project**, import the GitHub repository, set **Root Directory** to
`frontend`, and add the environment variable `API_URL = https://HOST`.

`API_URL` is read at build time. If you forget it, the build stops with a message telling you to set it.
After changing it, redeploy.

Vercel prints the address (`https://documind-xxxx.vercel.app`). Open it, sign in, chat, upload.

Things to know about this setup:

- **Visitors share one IP address** as far as the server can tell: it sees Vercel's servers, not the visitor.
  The per-IP login limit (10 a minute) is therefore shared by everyone. The per-account limit still works.
  For a demo this is fine; if people get "too many requests", raise `RATE_LIMIT_AUTH_PER_MINUTE` in `.env`.
- **Large uploads**: Vercel documents a 4.5 MB request limit for its functions, but uploads of 3, 6 and 12 MB
  went through the proxied `/api` path intact in a real test, so it does not apply here.
- If Vercel shows a login wall, switch off **Deployment Protection** for production in the project settings.

## 7. Updating later

```powershell
powershell -ExecutionPolicy Bypass -File infrastructure\lightsail\make-bundle.ps1
scp -i $key documind-bundle.tgz ubuntu@SERVER-IP:~/
```

```bash
tar -xzf documind-bundle.tgz -C documind && cd documind && bash infrastructure/lightsail/deploy.sh HOST
```

Your `.env`, data and uploaded files are kept. For website changes also run `npx vercel --prod` in `frontend`.

Add more people from **User Management** in the app, or create another workspace on the server:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env exec backend \
  python -m app.cli create-admin --email someone@example.com --name "Some One" --organization "Their Org"
```

## 8. Keep it safe and cheap

- **Sign-up stays closed.** Anyone who could register could use your API keys. Share logins only with people who
  should have them, and change the demo password afterwards (Settings → Password).
- **Set a monthly spending limit with each AI provider** (OpenAI: Settings → Limits; Google AI Studio / Cloud:
  budget alerts). Better: create separate keys just for the demo.
- **Cheap models only.** `CHAT_MODEL_IDS` limits what users can pick. Change it in `.env`, then run `deploy.sh`.
- **Restrict SSH** (port 22) to your own IP in the Lightsail firewall.
- **To stop paying**: take a snapshot (about $0.05 per GB-month, roughly $4 a month for the whole disk), delete
  the instance, and **release the static IP**. Restore later from the snapshot. Or just delete everything and
  redeploy (about 15 minutes), as the demo data is easy to recreate.

## 9. When something goes wrong

Run these on the server, in the `documind` folder:

```bash
docker compose -f infrastructure/docker-compose.yml --env-file .env ps                 # who is healthy
docker compose -f infrastructure/docker-compose.yml --env-file .env logs --tail 80 backend worker caddy
free -h                                                                                # memory and swap
```

| Symptom | Likely cause and fix |
|---|---|
| `deploy.sh` waits and then says the site did not come up | The host name does not point at the server yet (`nslookup HOST`), or ports 80/443 are closed in the Lightsail firewall. Caddy's log (`logs caddy`) names the certificate error. |
| `permission denied` talking to Docker | Log out and back in after `bootstrap-host.sh`, or prefix the command with `sudo`. |
| A container restarts, `Killed` in the logs, `free -h` shows no memory | Out of memory. Check the swap file exists (`swapon --show`), or move to the 8 GB plan ($44). |
| Chat says "Answer generation is not configured" | No API key in `.env`. Add `OPENAI_API_KEY=` or `GOOGLE_API_KEY=`, then run `deploy.sh HOST` again. |
| A model is greyed out in the picker | That provider's quota or spending limit is used up; the message says so. |
| Vercel build fails with "API_URL is not set" | Add the variable in the Vercel project settings (Environment Variables) and redeploy. |
| The Vercel site loads but sign-in or chat fails with 502/504 | Vercel cannot reach `https://HOST`: check the certificate is valid (open it in a browser) and the stack is up. |
| "Too many requests" at sign-in | Everyone shares Vercel's IP for the per-IP limit (section 6). Wait a minute or raise `RATE_LIMIT_AUTH_PER_MINUTE`. |
| First upload is slow | The AI models download on first use (about 1.5 GB). Later uploads reuse them. |

If you cannot solve it, copy the output of the three commands above; that is what is needed to diagnose it.
