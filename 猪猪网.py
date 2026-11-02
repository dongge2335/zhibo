import re
import json
import base64
import html as _html
from urllib.parse import unquote, quote
try:
    import requests as _rq
except Exception:
    _rq = None


class Spider:
    def __init__(self):
        self.site = "https://zzz9111.ttta1.cfd"
        self.name = "猪猪网"
        self.header = {
            "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-S9080) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36",
            "Referer": self.site,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self.s = self.session = self.sess = _rq.Session() if _rq else None

    def getName(self):
        return self.name

    def getDependence(self):
        return []

    def manualVideoCheck(self):
        return False

    def isVideoFormat(self, url):
        if not url:
            return False
        u = str(url).lower()
        return u.endswith((".m3u8", ".mp4", ".flv", ".mkv", ".ts", ".avi")) or "m3u8" in u

    def destroy(self):
        pass

    def action(self, action):
        return {}

    def init(self, extend=""):
        pass

    def _get(self, url):
        h = dict(self.header)
        if self.s is not None:
            try:
                r = self.s.get(url, headers=h, timeout=12, allow_redirects=True)
                if r.status_code < 400:
                    return r.text
            except Exception:
                pass
        try:
            import urllib.request
            req = urllib.request.Request(url, headers=h)
            with urllib.request.urlopen(req, timeout=12) as resp:
                return resp.read().decode("utf-8", "ignore")
        except Exception:
            return ""

    def _u(self, u):
        if not u:
            return u
        u = u.strip()
        if u.startswith("//"):
            return "https:" + u
        if u.startswith("http"):
            return u
        if u.startswith("/"):
            return self.site + u
        return self.site + "/" + u.lstrip("/")

    def _nav(self):
        html = self._get(self.site)
        out = []
        seen = set()
        m = re.search(r'<nav[^>]*>(.*?)</nav>', html, re.S)
        seg = m.group(1) if m else html
        for am in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>([^<]{1,20})</a>', seg):
            href, name = am.group(1), am.group(2).strip()
            if name in ("首页",) or not name:
                continue
            if "zzz9111.ttta1.cfd" not in href and not href.startswith("/"):
                continue
            path = re.sub(r'^https?://[^/]+', '', href)
            if not path or path == "/":
                continue
            if path in seen:
                continue
            seen.add(path)
            out.append({"type_id": path, "type_name": name})
        return out

    def _items(self, html):
        out = []
        seen = set()
        for m in re.finditer(r'href="(https://zzz9111\.ttta1\.cfd/v/(\d+)\.html)"', html):
            vid = m.group(2)
            if vid in seen:
                continue
            seg = html[m.end():m.end() + 700]
            im = re.search(r'<img[^>]+src="([^"]+)"', seg)
            if not im:
                continue
            tm = re.search(r'title="Permanent Link to ([^"]+)"', html[m.start():m.start() + 400])
            if not tm:
                tm = re.search(r'<img[^>]+title="([^"]+)"', seg)
            if not tm:
                continue
            seen.add(vid)
            out.append({
                "vod_id": vid + "|" + im.group(1),
                "vod_name": _html.unescape(tm.group(1)).strip(),
                "vod_pic": self._u(im.group(1)),
                "vod_remarks": "",
            })
        return out

    def _fetch_items(self, url):
        for _ in range(3):
            items = self._items(self._get(url))
            if items:
                return items
            try:
                import time
                time.sleep(1)
            except Exception:
                pass
        return []

    def homeContent(self, filter=None):
        cats = self._nav()
        if not cats:
            cats = [
                {"type_id": "/jp国产", "type_name": "jp国产"},
                {"type_id": "/jp网红主播", "type_name": "jp网红主播"},
                {"type_id": "/jp有码", "type_name": "jp有码"},
                {"type_id": "/jp无码", "type_name": "jp无码"},
            ]
        return {"class": cats, "filters": {}, "list": self._fetch_items(self.site)}

    def homeVideoContent(self):
        return {"list": self._fetch_items(self.site)}

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        tid = str(tid)
        url = self.site + tid
        if pg > 1:
            url = url.rstrip("/") + "/page/%d" % pg
        items = self._fetch_items(url)
        return {"list": items, "page": pg, "pagecount": 9999, "limit": 32, "total": 9999}

    def _playurl(self, html):
        m = re.search(r'data-payload="([^"]+)"', html)
        if m:
            try:
                raw = unquote(base64.b64decode(m.group(1)).decode("utf-8", "ignore"))
                m2 = re.search(r'[?&]url=(https?://[^"&\s]+)', raw)
                if m2:
                    return m2.group(1)
                m3 = re.search(r'(https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*)', raw)
                if m3:
                    return m3.group(1)
            except Exception:
                pass
        m4 = re.search(r'(https?://[^\s"\'\\<>]+\.m3u8[^\s"\'\\<>]*)', html)
        if m4:
            return m4.group(1)
        return ""

    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid or "")
        pic = ""
        if "|" in vid:
            vid, pic = vid.split("|", 1)
        url = self.site + "/v/%s.html" % vid
        html = self._get(url)
        name = ""
        tm = re.search(r'<title>([^<]+)</title>', html)
        if tm:
            name = _html.unescape(tm.group(1)).strip()
            if name.endswith(" - 猪猪网"):
                name = name[: -len(" - 猪猪网")]
        play = self._playurl(html)
        vurl = "播放$%s" % play if play else ""
        return {"list": [{
            "vod_id": vid,
            "vod_name": name,
            "vod_pic": self._u(pic),
            "vod_play_from": "猪猪网" if vurl else "",
            "vod_play_url": vurl,
        }]}

    def searchContent(self, key, quick=False, pg="1"):
        url = self.site + "/?s=" + quote(str(key))
        return {"list": self._fetch_items(url)}

    def playerContent(self, flag, ids, vipFlags=None):
        url = ids[0] if isinstance(ids, (list, tuple)) else ids
        url = str(url or "").strip()
        if not url.startswith("http"):
            url = self._u(url)
        return {"parse": 0, "url": url, "header": dict(self.header)}
