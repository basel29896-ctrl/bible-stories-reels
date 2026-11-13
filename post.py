"""Post the next unposted episode from posts.json to Instagram as a Reel (official Graph API, free).

The videos are hosted in this repo's GitHub release "videos" as epNN.mp4; Instagram fetches them by URL.
Runs daily from GitHub Actions (.github/workflows/post.yml); posts only when the last post is >= MIN_GAP_HOURS old,
which gives one post every 2 days.

Usage:
  python post.py --check     verify token + account, show token expiry and next post (posts nothing)
  python post.py --dry-run   show what would be posted next (no token needed)
  python post.py             post the next episode if the gap allows
  python post.py --force     post now even if the gap is shorter

Credentials: env IG_USER_ID / IG_TOKEN (GitHub Secrets), else config.json next to this file.
"""
import json, os, sys, time, urllib.error, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG = os.path.join(HERE, "config.json")
POSTS = os.path.join(HERE, "posts.json")
STATE = os.path.join(HERE, "state.json")
LOG = os.path.join(HERE, "post.log")
API_VERSION = "v27.0"
VIDEO_BASE = "https://github.com/basel29896-ctrl/bible-stories-reels/releases/download/videos"
MIN_GAP_HOURS = 40  # runs daily; this makes it every 2 days and stops a late run from posting twice in a row


def now():
    return datetime.now(timezone.utc)


def log(msg):
    line = f"{now():%Y-%m-%d %H:%M} UTC {msg}"
    print(line)
    with open(LOG, "a", encoding="utf8") as f:
        f.write(line + "\n")


def load(path, default):
    return json.load(open(path, encoding="utf8")) if os.path.exists(path) else default


def save_state(state):
    tmp = STATE + ".tmp"
    json.dump(state, open(tmp, "w", encoding="utf8"), indent=1)
    os.replace(tmp, STATE)


def credentials():
    if os.environ.get("IG_TOKEN"):
        return os.environ["IG_USER_ID"], os.environ["IG_TOKEN"]
    cfg = load(CONFIG, {})
    return cfg.get("ig_user_id"), cfg.get("access_token")


def api(method, path, params):
    params = dict(params, access_token=TOKEN)
    url = f"https://graph.facebook.com/{API_VERSION}/{path}"
    data = None
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
    else:
        data = urllib.parse.urlencode(params).encode()
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data, method=method), timeout=120) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{method} {path} -> HTTP {e.code}: {e.read().decode(errors='replace')}")


def token_expiry():
    exp = api("GET", "debug_token", {"input_token": TOKEN}).get("data", {}).get("expires_at", 0)
    return "never" if not exp else datetime.fromtimestamp(exp, timezone.utc).strftime("%Y-%m-%d")


def next_post(posts, state):
    done = {p["episode"] for p in state["posted"]}
    return next((p for p in posts if p["episode"] not in done), None)


def publish(ig, post):
    url = f"{VIDEO_BASE}/ep{post['episode']}.mp4"
    params = {"media_type": "REELS", "video_url": url, "caption": post["caption"], "share_to_feed": "true",
              "thumb_offset": "2500"}  # cover = episode title card, not the dark opening frame
    if post.get("trial"):
        # Trial Reel: non-followers first; Instagram shares it to followers if it performs well.
        params["trial_params"] = json.dumps({"graduation_strategy": "SS_PERFORMANCE"})
    cid = api("POST", f"{ig}/media", params)["id"]
    log(f"ep{post['episode']}: container {cid} from {url}" + (" (trial reel)" if post.get("trial") else ""))
    for _ in range(40):  # up to ~20 min of processing
        time.sleep(30)
        st = api("GET", cid, {"fields": "status_code,status"})
        if st.get("status_code") == "FINISHED":
            break
        if st.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"processing {st}")
    else:
        raise RuntimeError("processing did not finish in 20 minutes")
    return api("POST", f"{ig}/media_publish", {"creation_id": cid})["id"]


def main():
    global TOKEN
    sys.stdout.reconfigure(encoding="utf-8")  # captions contain emoji; Windows console defaults to cp1252
    args = set(sys.argv[1:])
    posts, state = load(POSTS, []), load(STATE, {"posted": []})
    post = next_post(posts, state)
    if "--dry-run" in args:
        print(f"posted {len(state['posted'])}/{len(posts)}; next: " + (f"ep{post['episode']} {post['title']}\n\n{post['caption']}" if post else "none"))
        return

    ig, TOKEN = credentials()
    if not ig or not TOKEN or "PASTE" in TOKEN:
        log("ERROR no Instagram credentials (IG_USER_ID/IG_TOKEN or config.json)")
        sys.exit(1)

    if "--check" in args:
        acct = api("GET", ig, {"fields": "username"})
        print(f"account: @{acct.get('username')}  token expires: {token_expiry()}  "
              f"posted {len(state['posted'])}/{len(posts)}  next: " + (f"ep{post['episode']} {post['title']}" if post else "none"))
        return

    if post is None:
        log("all episodes posted; nothing to do")
        return
    if state["posted"] and "--force" not in args:
        last = datetime.fromisoformat(state["posted"][-1]["at"])
        if now() - last < timedelta(hours=MIN_GAP_HOURS):
            print(f"skip: last post {last:%Y-%m-%d %H:%M} UTC is under {MIN_GAP_HOURS} h ago")
            return

    try:
        if token_expiry() != "never":
            log("WARNING access token has an expiry date; replace it with a Page token (SETUP.md)")
        # Guard against double posting after an ambiguous earlier failure.
        recent = api("GET", f"{ig}/media", {"fields": "id,caption", "limit": 10}).get("data", [])
        existing = next((m["id"] for m in recent if (m.get("caption") or "").strip() == post["caption"].strip()), None)
        media_id = existing or publish(ig, post)
    except Exception as e:
        log(f"ERROR ep{post['episode']}: {e}")
        sys.exit(1)
    state["posted"].append({"episode": post["episode"], "media_id": media_id, "at": now().isoformat(timespec="seconds")})
    save_state(state)
    log(f"posted ep{post['episode']} {post['title']} -> media {media_id}" + (" (found already on account)" if existing else ""))


TOKEN = None

if __name__ == "__main__":
    main()
