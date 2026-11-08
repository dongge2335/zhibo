# coding=utf-8
"""
独播库 - 酷9 v7 Python3 适配版 (IPTV网关模式)
源站: https://www.duboku.tv
API: https://api.dbokutv.com

适配说明:
1. 移除 base.spider 依赖, 改为独立类 + HTTP网关
2. urllib3 替换为标准库 urllib.request (内置Chaquopy即可运行)
3. 保留全部加密逻辑 (decode/sign/interleave/random_string)
4. 提供 /all.m3u 订阅, /play/<id> 播放解析, /search, /detail 等路由
5. 分组结构: 一级=分类(连续剧/电影/综艺/动漫), 二级=剧名, 频道=集数
6. 默认每分类拉取50部, 可用 ?limit=N 调整数量 (如 ?limit=20)
7. 持久化缓存: 首次生成后保存到 .duboku_cache/all.m3u, 后续秒回
8. 快速浏览模式: ?brief=1 (每部剧一个条目, 播第一集, 不展开集数)

使用:
  python3 独播库_酷9.py [端口]
  酷9订阅: http://localhost:8769/all.m3u
  更多剧集: http://localhost:8769/all.m3u?limit=100
  快速浏览: http://localhost:8769/all.m3u?brief=1
"""
import argparse
import base64
import gzip
import json
import os
import random
import ssl
import sys
import threading
import time
import urllib.request
import urllib.parse
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

__version__ = "1.0.0"

# ============ 全局变量 ============
server_port = 8769
server_start_time = 0
ssl_ctx = ssl._create_unverified_context()


# ============ 核心: 独播库API客户端 ============
class DubokuSpider:
    """独播库影视API客户端 - 纯标准库实现"""

    def __init__(self):
        self.host = "https://api.dbokutv.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Connection": "Keep-Alive",
            "Accept-Encoding": "gzip",
            "Referer": "https://www.duboku.tv/",
        }
        self.play_headers = {
            "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                           "AppleWebKit/537.36 (KHTML, like Gecko) "
                           "Chrome/121.0.0.0 Safari/537.36"),
            "Accept-Encoding": "gzip, deflate",
            "origin": "https://w.duboku.io",
            "referer": "https://w.duboku.io/",
        }
        self._cache = {}

    # ---- 加密工具 (原脚本逻辑完整保留) ----

    def decode(self, data):
        """独播库自定义base64解码: 每10字符反转, 点号替换等号"""
        if not data or not isinstance(data, str):
            return ""
        stripped_str = data.strip("'\"")
        if not stripped_str:
            return ""
        segment_length = 10
        try:
            reversed_segments = (
                stripped_str[i:i + segment_length][::-1]
                for i in range(0, len(stripped_str), segment_length)
            )
            processed_base64 = "".join(reversed_segments).replace(".", "=")
        except Exception:
            return ""
        try:
            padding_needed = 4 - len(processed_base64) % 4
            if padding_needed != 4:
                processed_base64 += "=" * padding_needed
            decoded_bytes = base64.b64decode(processed_base64, validate=True)
            return decoded_bytes.decode("utf-8")
        except Exception:
            return ""

    def sign(self, raw):
        """URL签名: 生成sign/token/ssid参数"""
        random.seed(int(time.time()))
        random_number = random.randint(0, 800000000)
        value_a = random_number + 100000000
        value_b = 900000000 - random_number
        current_unix_timestamp = int(time.time())
        unix_timestamp_str = str(current_unix_timestamp)
        interleaved_str = self.interleave_strings(
            "%d%d" % (value_a, value_b), unix_timestamp_str)
        ssid_base64_encoded = base64.b64encode(
            interleaved_str.encode()).decode().replace("=", ".")
        random_sign = self.random_string(60)
        random_token = self.random_string(38)
        return "%s?sign=%s&token=%s&ssid=%s" % (
            raw, random_sign, random_token, ssid_base64_encoded)

    def interleave_strings(self, first_str, second_str):
        """两个字符串交叉合并"""
        interleaved_chars = []
        min_length = min(len(first_str), len(second_str))
        for i in range(min_length):
            interleaved_chars.append(first_str[i])
            interleaved_chars.append(second_str[i])
        interleaved_chars.append(first_str[min_length:])
        interleaved_chars.append(second_str[min_length:])
        return "".join(interleaved_chars)

    def random_string(self, length):
        """生成指定长度的随机字符串"""
        character = ("abcdefghijklmnopqrstuvwxyz"
                     "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789")
        random.seed(int(time.time()) + len(character))
        return "".join(random.choice(character) for _ in range(length))

    # ---- HTTP请求 ----

    def _fetch(self, url, timeout=15, retries=2):
        """HTTP GET请求, 自动处理gzip, 返回JSON"""
        last_err = None
        for attempt in range(retries + 1):
            try:
                req = urllib.request.Request(url, headers=self.headers)
                with urllib.request.urlopen(req, timeout=timeout,
                                            context=ssl_ctx) as resp:
                    raw = resp.read()
                    # 处理gzip压缩
                    if resp.headers.get("Content-Encoding") == "gzip":
                        raw = gzip.decompress(raw)
                    return json.loads(raw.decode("utf-8"))
            except Exception as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.5 * (attempt + 1))
        print("[独播库 ERROR] GET %s: %s" % (url[:80], last_err),
              file=sys.stderr)
        return None

    # ---- 缓存 ----

    def _cache_get(self, key):
        item = self._cache.get(key)
        if not item:
            return None
        expire, data = item
        if time.time() > expire:
            self._cache.pop(key, None)
            return None
        return data

    def _cache_set(self, key, data, ttl=60):
        self._cache[key] = (time.time() + ttl, data)
        if len(self._cache) > 200:
            now = time.time()
            self._cache = {k: v for k, v in self._cache.items() if v[0] > now}

    # ---- 分类 ----

    def home_content(self):
        return {"class": [
            {"type_id": 2, "type_name": "连续剧"},
            {"type_id": 3, "type_name": "综艺"},
            {"type_id": 1, "type_name": "电影"},
            {"type_id": 4, "type_name": "动漫"},
        ]}

    # ---- 首页推荐 ----

    def home_videos(self):
        cache_key = "home"
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        response = self._fetch(self.sign("%s/home" % self.host))
        videos = []
        if response:
            for i in response:
                for j in i.get("VodList", []):
                    videos.append(self._format_vod(j))
        self._cache_set(cache_key, videos, ttl=300)
        return videos

    # ---- 分类列表 ----

    def category(self, tid, pg=1):
        cache_key = ("cat", tid, pg)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        page_param = "" if str(pg) == "1" else pg
        url = self.sign("%s/vodshow/%s--------%s---" % (
            self.host, tid, page_param))
        response = self._fetch(url)
        videos = []
        pagecount = 1
        if response and "VodList" in response:
            for i in response["VodList"]:
                videos.append(self._format_vod(i))
            try:
                for j in response.get("PaginationList", []):
                    if j["Type"] == "StartEnd":
                        pgcount = int(self.decode(
                            j.get("PId", j.get("PuId"))).split("-")[8])
                        if pgcount:
                            pagecount = pgcount
            except Exception:
                pass
            if pagecount == 1:
                try:
                    for j in response.get("PaginationList", []):
                        if j["Type"] == "ShortPage":
                            pgcount = int(j["Name"].split("/")[1])
                            if pgcount:
                                pagecount = pgcount
                except Exception:
                    pagecount = 1
        result = {"list": videos, "pagecount": pagecount, "page": pg}
        self._cache_set(cache_key, result, ttl=120)
        return result

    # ---- 搜索 ----

    def search(self, key, pg=1):
        cache_key = ("search", key, pg)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        url = "%s&wd=%s" % (
            self.sign("%s/vodsearch" % self.host),
            urllib.parse.quote(key))
        response = self._fetch(url)
        videos = []
        if response:
            for i in response:
                videos.append(self._format_vod(i, include_extra=True))
        result = {"list": videos, "page": pg}
        self._cache_set(cache_key, result, ttl=300)
        return result

    # ---- 详情 ----

    def detail(self, vod_id):
        cache_key = ("detail", vod_id)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        response = self._fetch(self.sign("%s%s" % (self.host, vod_id)))
        if not response:
            return None
        play_urls = [
            "%s$%s" % (i["EpisodeName"], self.decode(i["VId"]))
            for i in response.get("Playlist", [])
        ]
        video = {
            "vod_id": vod_id,
            "vod_name": response.get("Name", ""),
            "vod_pic": self.decode(response.get("TnId", "")),
            "vod_remarks": "评分：%s" % response.get("Rating", ""),
            "vod_year": response.get("ReleaseYear", ""),
            "vod_area": response.get("Region", ""),
            "vod_actor": ",".join(response.get("Actor", [])),
            "vod_director": response.get("Director", ""),
            "vod_content": response.get("Description", ""),
            "vod_play_from": "独播库",
            "vod_play_url": "#".join(play_urls),
            "type_name": "%s,%s,%s" % (
                response.get("Genre", ""),
                response.get("Scenario", ""),
                response.get("Language", "")),
        }
        self._cache_set(cache_key, video, ttl=600)
        return video

    # ---- 播放地址解析 ----

    def play(self, video_id):
        """解析真实播放地址, 返回 (url, headers)"""
        cache_key = ("play", video_id)
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached
        response = self._fetch(self.sign("%s%s" % (self.host, video_id)))
        if not response or "HId" not in response:
            return None, None
        url = self.decode(response["HId"])
        if not url:
            return None, None
        result = (url, self.play_headers)
        self._cache_set(cache_key, result, ttl=300)
        return result

    # ---- 格式化 ----

    def _format_vod(self, j, include_extra=False):
        vod = {
            "vod_id": self.decode(j.get("DId", j.get("DuId"))),
            "vod_name": j.get("Name", ""),
            "vod_pic": self.decode(j.get("TnId", "")),
            "vod_remarks": j.get("Tag", ""),
        }
        if include_extra:
            vod["vod_actor"] = j.get("Actor", "")
            vod["vod_score"] = j.get("Rating", "")
        return vod


# ============ 全局spider实例 ============
spider = None

# ============ M3U缓存 ============
_m3u_cache = {"content": "", "time": 0, "key": ""}
_m3u_cache_lock = None  # 运行时初始化
CACHE_TTL = 600  # 缓存10分钟
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         ".duboku_cache")
CACHE_FILE = os.path.join(CACHE_DIR, "all.m3u")
_generating = False  # 是否正在后台生成缓存


def load_m3u_cache():
    """启动时从文件加载缓存"""
    try:
        if os.path.exists(CACHE_FILE):
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                content = f.read()
            if content and "#EXTINF" in content:
                _m3u_cache["content"] = content
                _m3u_cache["time"] = os.path.getmtime(CACHE_FILE)
                _m3u_cache["key"] = "default"
                channels = content.count("#EXTINF")
                print("[独播库] 已加载本地缓存: %d 个频道" % channels,
                      file=sys.stderr)
    except Exception as e:
        print("[独播库] 缓存加载失败: %s" % e, file=sys.stderr)


def save_m3u_cache(content):
    """保存缓存到文件"""
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception as e:
        print("[独播库] 缓存保存失败: %s" % e, file=sys.stderr)


def get_cached_m3u(key="default"):
    """获取有效缓存, 无效返回None"""
    if (_m3u_cache["content"] and _m3u_cache["key"] == key
            and time.time() - _m3u_cache["time"] < CACHE_TTL):
        return _m3u_cache["content"]
    return None


def set_m3u_cache(content, key="default"):
    """设置缓存"""
    _m3u_cache["content"] = content
    _m3u_cache["time"] = time.time()
    _m3u_cache["key"] = key
    save_m3u_cache(content)


def generate_full_m3u(limit=50):
    """后台生成完整M3U缓存(展开集数)"""
    global _generating
    if _generating:
        return
    _generating = True
    try:
        print("[独播库] 后台开始生成完整缓存 (limit=%d)..." % limit,
              file=sys.stderr)
        t0 = time.time()
        lines = ["#EXTM3U"]
        cats = spider.home_content()
        for c in cats["class"]:
            tid = c["type_id"]
            tname = c["type_name"]
            result = spider.category(tid, 1)
            videos = result["list"][:limit]
            for v in videos:
                detail = spider.detail(v["vod_id"])
                if not detail or not detail.get("vod_play_url"):
                    continue
                episodes = detail["vod_play_url"].split("#")
                vod_name = detail["vod_name"]
                pic = detail.get("vod_pic", "")
                for ep_str in episodes:
                    if "$" not in ep_str:
                        continue
                    ep_name, video_id = ep_str.split("$", 1)
                    ch_name = ep_name if len(episodes) > 1 else vod_name
                    play_url = "http://127.0.0.1:%d/play/%s" % (
                        server_port, video_id)
                    attrs = (' tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                             'category="%s" group-title="%s"' % (
                                 video_id, ch_name, pic, tname, vod_name))
                    lines.append("#EXTINF:-1%s,%s" % (attrs, ch_name))
                    lines.append(play_url)
        result_m3u = "\n".join(lines) + "\n"
        total = result_m3u.count("#EXTINF")
        set_m3u_cache(result_m3u, "default")
        print("[独播库] 完整缓存生成完成: %d 个频道, 耗时 %d秒" % (
            total, int(time.time() - t0)), file=sys.stderr)
    except Exception as e:
        print("[独播库] 后台缓存生成失败: %s" % e, file=sys.stderr)
    finally:
        _generating = False


# ============ URL安全编码 ============
def safe_url(url):
    try:
        parts = urllib.parse.urlsplit(url)
        path = urllib.parse.quote(parts.path, safe="/%")
        query = urllib.parse.quote(parts.query, safe="=&%?")
        return urllib.parse.urlunsplit(
            (parts.scheme, parts.netloc, path, query, parts.fragment))
    except Exception:
        return url


# ============ HTTP Handler ============
class Handler(BaseHTTPRequestHandler):
    server_version = "Duboku/%s" % __version__

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="text/plain; charset=utf-8",
              head_only=False, extra_headers=None):
        data = body.encode("utf-8") if isinstance(body, str) else body
        try:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Cache-Control", "no-cache, no-store, max-age=0")
            if extra_headers:
                for k, v in extra_headers.items():
                    self.send_header(k, v)
            self.end_headers()
            if not head_only:
                self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_HEAD(self):
        self.do_GET(head_only=True)

    def do_GET(self, head_only=False):
        global spider
        parts = urllib.parse.urlsplit(self.path)
        path = parts.path
        query = urllib.parse.parse_qs(parts.query)
        host = self.headers.get("Host", "localhost:%d" % server_port)
        proto = self.headers.get("X-Forwarded-Proto", "http")

        # ---- 首页 ----
        if path in ("/", "/index.html"):
            cats = spider.home_content()
            base = "%s://%s" % (proto, host)
            text = (
                "====================================================\n"
                "独播库 IPTV 网关运行中 (端口: %d)\n"
                "版本: %s\n"
                "====================================================\n\n"
                "影视订阅(全集展开): %s/all.m3u\n"
                "快速浏览(每剧1条):  %s/all.m3u?brief=1\n"
                "单剧订阅:           %s/ep/<id>.m3u\n"
                "健康检查:           %s/health\n"
                "分类筛选:           %s/all.m3u?type=1 (1电影 2连续剧 3综艺 4动漫)\n"
                "数量限制:           %s/all.m3u?limit=5 (每分类取N部)\n"
                "搜索:               %s/search?key=关键词\n"
                "详情:               %s/detail/<vod_id>\n"
                "播放:               %s/play/<vod_id>?ep=集数\n\n"
                "二级分组: category=分类(一级), group-title=剧名(二级), 频道=集数\n"
                "订阅名称在酷9添加订阅时填写, 如: 独播库\n"
                "分类列表:\n"
                % (server_port, __version__,
                   base, base, base, base, base, base, base, base, base)
            )
            for c in cats["class"]:
                text += "  %s (type_id=%s)\n" % (c["type_name"], c["type_id"])
            self._send(200, text, head_only=head_only)
            return

        # ---- 健康检查 ----
        if path == "/health":
            uptime = int(time.time() - server_start_time)
            self._send(200, json.dumps({
                "ok": True,
                "version": __version__,
                "uptime_sec": uptime,
                "port": server_port,
                "routes": ["/", "/all.m3u", "/full.m3u", "/ep/<id>.m3u",
                           "/health", "/search", "/detail/<id>",
                           "/play/<id>?ep=N"],
            }, ensure_ascii=False, indent=2), "application/json; charset=utf-8",
                head_only=head_only)
            return

        # ---- 影视列表 M3U ----
        # 默认: 展开集数, 二级分组为"分类|剧名", 每集一个频道
        # ?brief=1: 快速浏览模式, 每部剧一个条目(播第一集)
        if path == "/all.m3u":
            type_id = query.get("type", [None])[0]
            page = int(query.get("page", ["1"])[0])
            search_key = query.get("search", [None])[0]
            brief = query.get("brief", ["0"])[0] == "1"
            limit = int(query.get("limit", ["50"])[0])

            # 完全无参数的默认请求走缓存, 避免重复生成
            is_default = (parts.query == "")
            degraded = False
            if is_default:
                cached = get_cached_m3u("default")
                if cached:
                    self._send(200, cached,
                               "application/vnd.apple.mpegurl",
                               head_only=head_only)
                    return
                # 无缓存: 并发实时生成(8线程, 50部约20-30秒)
                # 后台同时预生成, 双保险
                if not _generating:
                    threading.Thread(target=generate_full_m3u,
                                     args=(50,), daemon=True).start()

            lines = ["#EXTM3U"]

            def _fetch_details(videos, max_workers=8):
                """并发获取多部剧的详情, 返回 {vod_id: detail}"""
                details = {}
                lock = threading.Lock()
                sem = threading.Semaphore(max_workers)

                def worker(v):
                    with sem:
                        try:
                            d = spider.detail(v["vod_id"])
                            with lock:
                                details[v["vod_id"]] = d
                        except Exception:
                            pass

                threads = []
                for v in videos:
                    t = threading.Thread(target=worker, args=(v,))
                    threads.append(t)
                    t.start()
                for t in threads:
                    t.join()
                return details

            def _append_brief(v, category):
                """快速模式: 每部剧一个条目, 播第一集"""
                name = v["vod_name"]
                if v.get("vod_remarks"):
                    name = "%s [%s]" % (name, v["vod_remarks"])
                pic = v.get("vod_pic", "")
                play_url = "%s://%s/play/%s" % (proto, host, v["vod_id"])
                attrs = (' tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                         'category="%s" group-title="%s"' % (
                             v["vod_id"], name, pic, category, category))
                lines.append("#EXTINF:-1%s,%s" % (attrs, name))
                lines.append(play_url)

            def _append_full(v, category, detail=None):
                """完整模式: 一级=分类, 二级=剧名, 频道=集数"""
                if detail is None:
                    detail = spider.detail(v["vod_id"])
                if not detail or not detail.get("vod_play_url"):
                    return
                episodes = detail["vod_play_url"].split("#")
                vod_name = detail["vod_name"]
                pic = detail.get("vod_pic", "")
                for ep_str in episodes:
                    if "$" not in ep_str:
                        continue
                    ep_name, video_id = ep_str.split("$", 1)
                    # 多集只显示集数, 单集(电影)显示剧名
                    ch_name = ep_name if len(episodes) > 1 else vod_name
                    play_url = "%s://%s/play/%s" % (proto, host, video_id)
                    attrs = (' tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                             'category="%s" group-title="%s"' % (
                                 video_id, ch_name, pic, category, vod_name))
                    lines.append("#EXTINF:-1%s,%s" % (attrs, ch_name))
                    lines.append(play_url)

            if search_key:
                result = spider.search(search_key, page)
                for v in result["list"]:
                    if brief:
                        _append_brief(v, "搜索")
                    else:
                        _append_full(v, "搜索")
            elif type_id:
                cats = spider.home_content()
                type_name = type_id
                for c in cats["class"]:
                    if str(c["type_id"]) == str(type_id):
                        type_name = c["type_name"]
                        break
                result = spider.category(type_id, page)
                videos = result["list"] if brief else result["list"][:limit]
                if brief:
                    for v in videos:
                        _append_brief(v, type_name)
                else:
                    details = _fetch_details(videos)
                    for v in videos:
                        _append_full(v, type_name, details.get(v["vod_id"]))
            else:
                cats = spider.home_content()
                for c in cats["class"]:
                    tid = c["type_id"]
                    tname = c["type_name"]
                    result = spider.category(tid, 1)
                    videos = result["list"] if brief else result["list"][:limit]
                    if brief:
                        for v in videos:
                            _append_brief(v, tname)
                    else:
                        details = _fetch_details(videos)
                        for v in videos:
                            _append_full(v, tname, details.get(v["vod_id"]))

            total = len([l for l in lines if l.startswith("#EXTINF")])
            print("[独播库] all.m3u: %d 个频道 (%s)" % (
                total, "brief" if brief else "full"), file=sys.stderr)
            result_m3u = "\n".join(lines) + "\n"
            # 默认请求且为完整模式(非降级brief)时保存缓存
            if is_default and not degraded and total > 0:
                set_m3u_cache(result_m3u, "default")
                print("[独播库] 已缓存到本地: %s" % CACHE_FILE,
                      file=sys.stderr)
            self._send(200, result_m3u,
                       "application/vnd.apple.mpegurl", head_only=head_only)
            return

        # ---- 单部剧所有集数 M3U ----
        if path.startswith("/ep/") and path.endswith(".m3u"):
            vod_id = path[len("/ep/"):-len(".m3u")]
            detail = spider.detail(vod_id)
            lines = ["#EXTM3U"]
            if detail and detail.get("vod_play_url"):
                episodes = detail["vod_play_url"].split("#")
                vod_name = detail["vod_name"]
                pic = detail.get("vod_pic", "")
                for i, ep_str in enumerate(episodes):
                    if "$" not in ep_str:
                        continue
                    ep_name, video_id = ep_str.split("$", 1)
                    # 多集只显示集数, 单集显示剧名
                    ch_name = ep_name if len(episodes) > 1 else vod_name
                    play_url = "%s://%s/play/%s" % (proto, host, video_id)
                    attrs = (' tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                             'category="独播库" group-title="%s"' % (
                                 video_id, ch_name, pic, vod_name))
                    lines.append("#EXTINF:-1%s,%s" % (attrs, ch_name))
                    lines.append(play_url)
            self._send(200, "\n".join(lines) + "\n",
                       "application/vnd.apple.mpegurl", head_only=head_only)
            return

        # ---- 完整影视库 M3U (展开所有集数, 每集一个频道) ----
        if path == "/full.m3u":
            lines = ["#EXTM3U"]
            cats = spider.home_content()
            max_per_cat = int(query.get("limit", ["15"])[0])
            for c in cats["class"]:
                tid = c["type_id"]
                tname = c["type_name"]
                result = spider.category(tid, 1)
                videos = result["list"][:max_per_cat]
                for v in videos:
                    detail = spider.detail(v["vod_id"])
                    if not detail or not detail.get("vod_play_url"):
                        continue
                    episodes = detail["vod_play_url"].split("#")
                    vod_name = detail["vod_name"]
                    pic = detail.get("vod_pic", "")
                    for i, ep_str in enumerate(episodes):
                        if "$" not in ep_str:
                            continue
                        ep_name, video_id = ep_str.split("$", 1)
                        # 多集只显示集数, 单集(电影)显示剧名
                        ch_name = ep_name if len(episodes) > 1 else vod_name
                        play_url = "%s://%s/play/%s" % (proto, host, video_id)
                        attrs = (' tvg-id="%s" tvg-name="%s" tvg-logo="%s" '
                                 'category="%s" group-title="%s"' % (
                                     video_id, ch_name, pic, tname, vod_name))
                        lines.append("#EXTINF:-1%s,%s" % (attrs, ch_name))
                        lines.append(play_url)
            total = len([l for l in lines if l.startswith("#EXTINF")])
            print("[独播库] full.m3u: %d 个频道" % total, file=sys.stderr)
            self._send(200, "\n".join(lines) + "\n",
                       "application/vnd.apple.mpegurl", head_only=head_only)
            return

        # ---- 搜索 ----
        if path == "/search":
            key = query.get("key", [""])[0]
            if not key:
                self._send(400, "missing key\n", head_only=head_only)
                return
            result = spider.search(key)
            self._send(200, json.dumps(result, ensure_ascii=False, indent=2),
                       "application/json; charset=utf-8", head_only=head_only)
            return

        # ---- 详情 ----
        if path.startswith("/detail/"):
            vod_id = path[len("/detail/"):]
            detail = spider.detail(vod_id)
            if detail:
                self._send(200, json.dumps(detail, ensure_ascii=False, indent=2),
                           "application/json; charset=utf-8",
                           head_only=head_only)
            else:
                self._send(404, "not found\n", head_only=head_only)
            return

        # ---- 播放地址解析 ----
        if path.startswith("/play/"):
            vod_id = path[len("/play/"):]
            ep_num = query.get("ep", [None])[0]
            try:
                # 支持直接传入播放页ID (/vodplay/xxx) 或详情页ID (/voddetail/xxx)
                if "/vodplay/" in vod_id:
                    video_id = vod_id
                    url, headers = spider.play(video_id)
                    ep_name = "指定集"
                else:
                    # 详情页ID: 先取详情，再按集数取播放ID
                    detail = spider.detail(vod_id)
                    if not detail or not detail.get("vod_play_url"):
                        self._send(503, "无法获取影视详情\n",
                                   head_only=head_only)
                        return
                    episodes = detail["vod_play_url"].split("#")
                    # 按集数选择: ep=1 第一集, ep=2 第二集...
                    idx = 0
                    if ep_num:
                        try:
                            idx = max(0, int(ep_num) - 1)
                            idx = min(idx, len(episodes) - 1)
                        except ValueError:
                            idx = 0
                    ep_str = episodes[idx]
                    if "$" not in ep_str:
                        self._send(503, "播放地址格式异常\n",
                                   head_only=head_only)
                        return
                    ep_name, video_id = ep_str.split("$", 1)
                    url, headers = spider.play(video_id)

                if not url:
                    self._send(503, "无法获取播放地址\n", head_only=head_only)
                    return
                # 302重定向到真实地址
                self._send(302, "", head_only=head_only,
                           extra_headers={"Location": safe_url(url)})
                print("[独播库] 播放: %s -> %s" % (ep_name, url[:60]),
                      file=sys.stderr)
            except Exception as e:
                print("[独播库 ERROR] play: %s" % e, file=sys.stderr)
                self._send(500, "播放解析异常: %s\n" % e,
                           head_only=head_only)
            return

        # ---- 404 ----
        self._send(404, "not found\n", head_only=head_only)


# ============ 主入口 ============
def main():
    global server_port, server_start_time, spider

    ap = argparse.ArgumentParser(description="独播库 IPTV 网关 (酷9适配版)")
    ap.add_argument("port", nargs="?", type=int, default=8769,
                    help="监听端口 (默认 8769)")
    ap.add_argument("--bind", default="0.0.0.0",
                    help="监听地址 (默认 0.0.0.0)")
    args = ap.parse_args()

    server_port = args.port
    server_start_time = time.time()
    spider = DubokuSpider()
    load_m3u_cache()  # 启动时加载本地缓存, 避免酷9首次订阅超时

    # 后台预生成完整缓存(如无有效缓存)
    if not get_cached_m3u("default"):
        threading.Thread(target=generate_full_m3u, args=(50,),
                         daemon=True).start()

    srv = ThreadingHTTPServer((args.bind, args.port), Handler)
    print("=" * 52)
    print("独播库 IPTV 网关运行中 (端口: %d)" % args.port)
    print("版本: %s" % __version__)
    print("=" * 52)
    print("影视订阅:   http://localhost:%d/all.m3u" % args.port)
    print("分类筛选:   http://localhost:%d/all.m3u?type=1" % args.port)
    print("  1=电影  2=连续剧  3=综艺  4=动漫")
    print("搜索:       http://localhost:%d/search?key=关键词" % args.port)
    print("健康检查:   http://localhost:%d/health" % args.port)
    print("=" * 52)

    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[系统] 服务已停止")
        srv.server_close()


if __name__ == "__main__":
    main()