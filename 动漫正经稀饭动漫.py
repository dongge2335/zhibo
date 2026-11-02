# coding=utf-8
# ================== 动漫·正经·稀饭动漫 · TVBox ==================
# 🏷️ 源头：网站  https://next.xifanacg.com
# 👤 作者：无聊  |  📅 2026-11-01
# 🏷️ 平台：TVBox / 影视仓  ·  csp_Spider
# 🔍 站型：Next.js 15 RSC(列表不在HTML里) + Supabase 公开 REST(anon key) + Edge Function 签链
# 🎬 关键：animes REST 直查 / issue-web-playback 签 2 小时直链 / available_at 过滤未播集 / 播放头 UA=TVBox
# 📦 内容：视频（连载新番 / 完结旧番 / 剧场版 / 欧美动画）
# ==========================================================
import sys
import json
import time
import gzip
import ssl
import re
import urllib.request
import urllib.error
import http.client
from urllib.parse import quote, urlsplit

sys.path.append('..')
try:
    from base.spider import Spider as BaseSpider
except Exception:
    class BaseSpider(object):
        pass

API = "https://api.xifanacg.com"
REST = API + "/rest/v1"
SITE = "https://next.xifanacg.com"
KEY = "sb_publishable_OBIVAWACIX6lPXrO98_z24_HcsmalkA"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
PLAY_UA = "TVBox"
LIMIT = 24

# 分类（g=按 type_id 大类，h=全站排序榜）
GROUPS = {
    "g1": {"name": "🌸 连载新番", "type_id": 1, "order": "updated_at.desc"},
    "g2": {"name": "📺 完结旧番", "type_id": 2, "order": "updated_at.desc"},
    "g3": {"name": "🎬 剧场版",   "type_id": 3, "order": "release_year.desc"},
    "g4": {"name": "🌍 欧美动画", "type_id": 4, "order": "release_year.desc"},
    "h1": {"name": "🔥 热门排行", "type_id": 0, "order": "view_count.desc"},
    "h2": {"name": "🆕 最近更新", "type_id": 0, "order": "updated_at.desc"},
    "h3": {"name": "⭐ 高分佳作", "type_id": 0, "order": "bangumi_score.desc.nullslast"},
}

FIELDS = ("id,title,cover_url,current_episodes,total_episodes,"
          "release_year,remarks,is_finished,type_id,meta_tags,view_count,bangumi_score")

Y2 = ["2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017",
      "2016", "2015", "2014", "2013", "2012", "2011", "2010", "2009", "2008", "2007",
      "2006", "2005", "2004", "2003", "2002", "2001", "2000", "1999", "1998", "1997",
      "1996", "1995", "1994", "1993", "1992", "1991", "1989", "1987", "1985", "1983",
      "1979", "1974", "1972"]
Y3 = ["2026", "2025", "2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017",
      "2016", "2015", "2014", "2013", "2012", "2011", "2010", "2009", "2008", "2007",
      "2006", "2005", "2004", "2003", "2002", "2001", "2000", "1999", "1998", "1997",
      "1996", "1995", "1994", "1993", "1992", "1991", "1990", "1989", "1988", "1987",
      "1986", "1985", "1984", "1983", "1982", "1973", "1970", "1969"]
Y4 = ["2024", "2023", "2022", "2021", "2020", "2019", "2018", "2017", "2015", "2013"]

T1 = ["漫画改", "奇幻", "小说改", "战斗", "穿越", "恋爱", "科幻", "喜剧", "原创"]
T2 = ["漫画改", "小说改", "奇幻", "战斗", "恋爱", "原创", "校园", "日常", "后宫", "百合",
      "游戏改", "科幻", "悬疑", "穿越", "机战", "冒险", "运动", "喜剧", "推理", "少女向",
      "音乐", "职场"]
T3 = ["漫画改", "原创", "科幻", "奇幻", "战斗", "小说改", "恋爱", "百合", "冒险",
      "校园", "游戏改", "音乐", "后宫", "机战", "剧情", "日常"]
T4 = ["欧美", "美国", "科幻", "原创"]

FM = {"tv": "TV", "movie": "剧场版", "ova": "OVA", "web": "WEB"}
SORTS = ["最新更新", "播放最多", "评分最高"]


def _opt(lst):
    return [{"n": "全部", "v": ""}] + [{"n": x, "v": x} for x in lst]


def _re_int(txt, key, default):
    try:
        i = txt.find('"%s"' % key)
        if i < 0:
            return default
        seg = txt[i + len(key) + 3:]
        n = ""
        for ch in seg:
            if ch.isdigit():
                n += ch
            elif n:
                break
        return int(n) if n else default
    except Exception:
        return default


class Spider(BaseSpider):

    _POOL = {}

    def __init__(self):
        self._pool = {}

    def getName(self):
        return "稀饭动漫"

    def init(self, extend=""):
        self.extend = extend or ""

    def getDependence(self):
        return []

    def destroy(self):
        for c in list(self._pool.values()):
            try:
                c.close()
            except Exception:
                pass
        self._pool = {}

    def isVideoFormat(self, url):
        return any(x in (url or '').lower() for x in ['.m3u8', '.mp4', '.flv', '.mkv', '.ts'])

    def manualVideoCheck(self):
        return False

    # ---------------- 网络层：http.client 连接池 ----------------
    def _conn(self, host, timeout):
        c = self._pool.get(host)
        if c is not None:
            try:
                if c.sock is not None:
                    return c
            except Exception:
                pass
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        c = http.client.HTTPSConnection(host, timeout=timeout, context=ctx)
        self._pool[host] = c
        return c

    def _raw(self, url, body=None, headers=None, timeout=15):
        u = urlsplit(url)
        host = u.hostname
        path = (u.path or "/")
        if u.query:
            path += "?" + u.query
        for attempt in (0, 1):
            c = self._conn(host, timeout)
            try:
                c.request("GET" if body is None else "POST", path,
                          body=body, headers=headers or {})
                r = c.getresponse()
                raw = r.read()
                hdrs = dict((k.lower(), v) for k, v in r.getheaders())
                return r.status, hdrs, raw
            except Exception:
                try:
                    c.close()
                except Exception:
                    pass
                self._pool.pop(host, None)
                if attempt:
                    raise
        raise IOError("conn failed")

    def _req(self, url, data=None, timeout=15, retry=2):
        headers = {
            "User-Agent": UA,
            "apikey": KEY,
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Connection": "keep-alive",
            "Origin": SITE,
            "Referer": SITE + "/",
        }
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode("utf-8")
        else:
            body = None
        for i in range(retry + 1):
            try:
                code, hdrs, raw = self._raw(url, body=body, headers=headers, timeout=timeout)
                if (hdrs.get("content-encoding") or "").lower() == "gzip":
                    raw = gzip.decompress(raw)
                txt = raw.decode("utf-8", "ignore")
                if code == 404:
                    return ""
                if code == 429:
                    time.sleep(min(_re_int(txt, "retry_after", 6) + 1, 20))
                    continue
                if code >= 500:
                    time.sleep(1 + i)
                    continue
                return txt
            except Exception:
                time.sleep(1 + i)
        return ""

    def _json(self, path, timeout=15):
        txt = self._req(REST + path, timeout=timeout)
        if not txt:
            return None
        try:
            return json.loads(txt)
        except Exception:
            return None

    # ---------------- 取流 ----------------
    def _post_play(self, ep_id):
        txt = self._req(API + "/functions/v1/issue-web-playback",
                        data={"action": "fallback", "episode_id": int(ep_id)},
                        timeout=20, retry=3)
        if not txt:
            return "", []
        try:
            d = json.loads(txt)
        except Exception:
            return "", []
        if not d.get("ok"):
            return "", []
        main = d.get("url") or ""
        cands = []
        for c in (d.get("candidates") or []):
            u = c.get("url") or ""
            if not u or u in cands:
                continue
            cands.append(u)
        if main and main not in cands:
            cands.insert(0, main)
        return main, cands

    @staticmethod
    def _pick(cands, line):
        """0=MP4直链优先 1=HLS优先；都没有按 mp4>hls>网盘 兜底"""
        mp4, hls, pan = [], [], []
        for u in cands:
            low = u.lower()
            if ".m3u8" in low:
                hls.append(u)
            elif "apn.moedot.net" in low:
                pan.append(u)
            else:
                mp4.append(u)
        order = (hls + mp4 + pan) if line == 1 else (mp4 + hls + pan)
        return order[0] if order else ""

    def _follow_302(self, url, hops=5):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        cur = url
        for _ in range(hops):
            try:
                r = urllib.request.Request(cur, headers={"User-Agent": PLAY_UA, "Range": "bytes=0-0"},
                                           method="GET")
                resp = urllib.request.urlopen(r, timeout=12, context=ctx)
                if getattr(resp, "status", 200) in (200, 206):
                    return cur
                loc = resp.headers.get("Location")
                if not loc:
                    return cur
                cur = loc
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location") if e.headers else None
                if e.code in (301, 302, 303, 307, 308) and loc:
                    cur = loc
                    continue
                return url
            except Exception:
                return url
        return cur

    # ---------------- 首页 ----------------
    def homeContent(self, filter):
        result = {"class": [], "filters": {}}
        for tid in ("g1", "g2", "g3", "g4", "h1", "h2", "h3"):
            result["class"].append({"type_id": tid, "type_name": GROUPS[tid]["name"]})

        result["filters"]["g1"] = [
            {"key": "tag", "name": "题材", "value": _opt(T1)},
            {"key": "sort", "name": "排序", "value": _opt(SORTS)},
        ]
        result["filters"]["g2"] = [
            {"key": "year", "name": "年份", "value": _opt(Y2)},
            {"key": "tag", "name": "题材", "value": _opt(T2)},
            {"key": "fmt", "name": "形式", "value": _opt(["tv", "ova", "web"])},
            {"key": "sort", "name": "排序", "value": _opt(SORTS)},
        ]
        result["filters"]["g3"] = [
            {"key": "year", "name": "年份", "value": _opt(Y3)},
            {"key": "tag", "name": "题材", "value": _opt(T3)},
            {"key": "fmt", "name": "形式", "value": _opt(["movie", "ova"])},
            {"key": "sort", "name": "排序", "value": _opt(["上映时间", "评分最高", "播放最多"])},
        ]
        result["filters"]["g4"] = [
            {"key": "year", "name": "年份", "value": _opt(Y4)},
            {"key": "tag", "name": "题材", "value": _opt(T4)},
            {"key": "sort", "name": "排序", "value": _opt(["上映时间", "评分最高"])},
        ]
        for tid in ("h1", "h2", "h3"):
            result["filters"][tid] = [
                {"key": "year", "name": "年份", "value": _opt(Y2)},
                {"key": "tag", "name": "题材", "value": _opt(T2)},
            ]

        result["list"] = self._videos(
            "/animes?select=%s&is_published=is.true&order=updated_at.desc&limit=%d"
            % (FIELDS, LIMIT))
        return result

    def homeVideoContent(self):
        return {"list": self.homeContent(False).get("list") or []}

    # ---------------- 列表 ----------------
    def _videos(self, path):
        data = self._json(path)
        out = []
        if not isinstance(data, list):
            return out
        for it in data:
            try:
                aid = it.get("id")
                if not aid:
                    continue
                cur = it.get("current_episodes") or 0
                tot = it.get("total_episodes") or 0
                if it.get("remarks"):
                    rem = it.get("remarks")
                elif it.get("is_finished"):
                    rem = "已完结" if not tot else "全 %d 集" % tot
                elif tot:
                    rem = "更新至 %d / %d 集" % (cur, tot)
                else:
                    rem = "更新至 %d 集" % cur
                out.append({
                    "vod_id": str(aid),
                    "vod_name": it.get("title") or "",
                    "vod_pic": it.get("cover_url") or "",
                    "vod_remarks": rem,
                    "vod_year": str(it.get("release_year") or ""),
                })
            except Exception:
                continue
        return out

    def _build_path(self, tid, pg, extend):
        g = GROUPS.get(tid) or GROUPS["g2"]
        q = ["is_published=is.true"]
        if g["type_id"]:
            q.append("type_id=eq.%d" % g["type_id"])
        order = g["order"]

        ext = extend if isinstance(extend, dict) else {}
        if ext.get("year"):
            q.append("release_year=eq.%s" % quote(str(ext["year"])))
        if ext.get("fmt"):
            q.append("format=eq.%s" % quote(str(ext["fmt"])))
        if ext.get("tag"):
            # Supabase 数组列过滤必须用花括号
            q.append("meta_tags=cs.{%s}" % quote(str(ext["tag"])))
        srt = ext.get("sort") or ""
        if srt == "播放最多":
            order = "view_count.desc"
        elif srt == "评分最高":
            order = "bangumi_score.desc.nullslast"
        elif srt in ("最新更新", "上映时间"):
            order = "release_year.desc" if g["type_id"] == 3 else "updated_at.desc"

        offset = (pg - 1) * LIMIT
        return "/animes?select=%s&%s&order=%s&limit=%d&offset=%d" % (
            FIELDS, "&".join(q), order, LIMIT, offset)

    def categoryContent(self, tid, pg=1, filter=None, extend=None):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        if pg < 1:
            pg = 1
        path = self._build_path(tid, pg, extend)
        vlist = self._videos(path)
        if not vlist and (extend or {}).get("tag") and pg == 1:
            # 标签+评分组合会返回空，降级只用标签
            ext = dict(extend)
            ext.pop("sort", None)
            vlist = self._videos(self._build_path(tid, pg, ext))
        return {
            "list": vlist,
            "page": pg,
            "pagecount": 1 if len(vlist) < LIMIT else 9999,
            "limit": LIMIT,
            "total": (pg - 1) * LIMIT + len(vlist),
        }

    def searchContent(self, key, quick=False, pg=1):
        try:
            pg = int(pg)
        except Exception:
            pg = 1
        k = (key or "").strip()
        if not k:
            return {"list": [], "page": 1, "pagecount": 0, "limit": LIMIT, "total": 0}
        offset = (pg - 1) * LIMIT
        enc = quote(k, safe="")
        path = ("/animes?select=%s&or=(search_title.ilike.*%s*,title.ilike.*%s*)"
                "&order=view_count.desc&limit=%d&offset=%d"
                % (FIELDS, enc, enc, LIMIT, offset))
        vlist = self._videos(path)
        return {
            "list": vlist,
            "page": pg,
            "pagecount": 1 if len(vlist) < LIMIT else 9999,
            "limit": LIMIT,
            "total": offset + len(vlist),
        }

    # ---------------- 详情 ----------------
    def detailContent(self, ids):
        vid = ids[0] if isinstance(ids, (list, tuple)) else ids
        vid = str(vid or "").strip()
        if not vid.isdigit():
            return {"list": []}
        d = self._json(
            "/animes?select=id,title,title_original,description,cover_url,director,actors,"
            "total_episodes,current_episodes,release_year,release_date,region,meta_tags,"
            "is_finished,bangumi_score,view_count,remarks,type_id,format&id=eq.%s" % quote(vid))
        if not isinstance(d, list) or not d:
            return {"list": []}
        a = d[0]
        title = a.get("title") or ""
        jp = a.get("title_original") or ""
        tags = [t for t in (a.get("meta_tags") or []) if t not in ("TV", "OVA", "WEB", "剧场版", "日本")]
        if a.get("format") in FM:
            tags.insert(0, FM[a["format"]])
        if a.get("release_year"):
            tags.insert(0, str(a["release_year"]))

        desc = a.get("description") or ""
        if a.get("bangumi_score"):
            desc = "【Bangumi %s 分 · 播放 %s · %s】\n%s" % (
                a["bangumi_score"], a.get("view_count") or 0,
                a.get("release_date") or "", desc)
        if jp and jp != title:
            desc = "【原名】%s\n%s" % (jp, desc)

        eps = self._json(
            "/episodes?select=id,episode_number,title,available_at"
            "&anime_id=eq.%s&order=episode_number.asc" % quote(vid)) or []
        # available_at 为空 = 该集还没上线，取流必 404
        real = [e for e in eps if e.get("available_at") and e.get("id")]

        labels, eids = [], []
        for e in real:
            try:
                num = int(e.get("episode_number") or 0)
            except Exception:
                num = 0
            lab = ("第%02d集" % num) if num else "正片"
            t = (e.get("title") or "").strip()
            if t:
                lab += " " + t
            labels.append(lab.replace("$", " "))
            eids.append(str(e["id"]))

        play_from, play_url = [], []
        if eids:
            play_from = ["🎬 高清直链", "📺 HLS 1080P"]
            for ln in (0, 1):
                play_url.append("#".join("%s$%d|%s" % (lb, ln, ei)
                                         for lb, ei in zip(labels, eids)))

        actors = a.get("actors") or []
        out = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": a.get("cover_url") or "",
            "type_name": " / ".join(tags[:3]) or "动漫",
            "vod_year": str(a.get("release_year") or ""),
            "vod_area": a.get("region") or "日本",
            "vod_remarks": (a.get("remarks") or ("已完结" if a.get("is_finished")
                                                else "更新至 %s 集" % (a.get("current_episodes") or 0))),
            "vod_actor": ",".join(actors) if isinstance(actors, list) else (actors or ""),
            "vod_director": a.get("director") or "",
            "vod_content": desc.strip(),
            "vod_play_from": "$$$".join(play_from),
            "vod_play_url": "$$$".join(play_url),
        }
        return {"list": [out]}

    # ---------------- 播放 ----------------
    def playerContent(self, flag, id, vipFlags=None):
        raw = str(id or "").strip()
        if raw.startswith("http"):
            return {"parse": 0, "url": raw, "header": {"User-Agent": PLAY_UA, "Referer": SITE + "/"}}
        line = 0
        pid = raw
        if "|" in raw:
            head, pid = raw.split("|", 1)
            try:
                line = int(head)
            except Exception:
                line = 0
        if not pid.isdigit():
            return {"parse": 1, "url": raw, "header": {}}
        main, cands = self._post_play(pid)
        if not cands:
            time.sleep(2)
            main, cands = self._post_play(pid)
        if not cands:
            return {"parse": 1, "url": SITE + "/anime/%s" % pid, "header": {}}
        url = self._pick(cands, line) or main
        if "apn.moedot.net" in url:
            url = self._follow_302(url) or url
        return {"parse": 0, "url": url,
                "header": {"User-Agent": PLAY_UA, "Referer": SITE + "/"}}
