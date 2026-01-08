import json
import os
import queue
import re
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import quote_plus, urljoin
from xml.etree import ElementTree as ET

import requests
from bs4 import BeautifulSoup
from flask import (
    Flask,
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import func
from werkzeug.security import check_password_hash, generate_password_hash


db = SQLAlchemy()


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def as_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


class User(db.Model):
    __tablename__ = "users"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(256), nullable=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class CrawlerSource(db.Model):
    __tablename__ = "crawler_sources"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False)
    type = db.Column(db.String(32), nullable=False, default="baidu")
    enabled = db.Column(db.Boolean, nullable=False, default=True)
    config_json = db.Column(db.Text, nullable=False, default="{}")
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class CollectedItem(db.Model):
    __tablename__ = "collected_items"

    id = db.Column(db.Integer, primary_key=True)
    external_id = db.Column(db.String(64), nullable=False, index=True)
    title = db.Column(db.String(512), nullable=False)
    url = db.Column(db.String(2048), nullable=False)
    source = db.Column(db.String(128), nullable=False, default="unknown")
    cover_url = db.Column(db.String(2048), nullable=True)
    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    collected_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    keyword = db.Column(db.String(128), nullable=False, default="")
    saved = db.Column(db.Boolean, nullable=False, default=False)


class ModelConfig(db.Model):
    __tablename__ = "model_configs"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(128), nullable=False, default="default")
    base_url = db.Column(db.String(512), nullable=False, default="https://api.openai.com/v1")
    api_key = db.Column(db.String(512), nullable=False, default="")
    model = db.Column(db.String(128), nullable=False, default="gpt-4o-mini")
    system_prompt = db.Column(db.Text, nullable=False, default="你是政企舆情分析助手。")
    enabled = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    updated_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now, onupdate=utc_now)


class TokenUsage(db.Model):
    __tablename__ = "token_usage"

    id = db.Column(db.Integer, primary_key=True)
    model_config_id = db.Column(db.Integer, db.ForeignKey("model_configs.id"), nullable=True)
    input_tokens = db.Column(db.Integer, nullable=False, default=0)
    output_tokens = db.Column(db.Integer, nullable=False, default=0)
    total_tokens = db.Column(db.Integer, nullable=False, default=0)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


class KeywordSearch(db.Model):
    __tablename__ = "keyword_searches"

    id = db.Column(db.Integer, primary_key=True)
    keyword = db.Column(db.String(128), unique=True, nullable=False, index=True)
    search_count = db.Column(db.Integer, nullable=False, default=0)
    last_searched_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)
    created_at = db.Column(db.DateTime(timezone=True), nullable=False, default=utc_now)


@dataclass
class StreamEvent:
    event: str
    data: Dict[str, Any]


class CollectStream:
    def __init__(self) -> None:
        self._queues: Dict[str, "queue.Queue[StreamEvent]"] = {}
        self._lock = threading.Lock()

    def create_channel(self) -> str:
        channel_id = uuid.uuid4().hex
        with self._lock:
            self._queues[channel_id] = queue.Queue()
        return channel_id

    def put(self, channel_id: str, event: StreamEvent) -> None:
        with self._lock:
            q = self._queues.get(channel_id)
        if q is not None:
            q.put(event)

    def close(self, channel_id: str) -> None:
        with self._lock:
            q = self._queues.pop(channel_id, None)
        if q is not None:
            q.put(StreamEvent(event="close", data={}))

    def events(self, channel_id: str) -> Iterable[StreamEvent]:
        with self._lock:
            q = self._queues.get(channel_id)
        if q is None:
            return iter(())

        def gen() -> Iterable[StreamEvent]:
            while True:
                event = q.get()
                yield event
                if event.event == "close":
                    break

        return gen()


collect_stream = CollectStream()

_cover_cache: Dict[str, Optional[str]] = {}
_cover_cache_lock = threading.Lock()

_baidu_hot_cache: Dict[str, Any] = {"ts": 0.0, "payload": None}
_baidu_hot_cache_lock = threading.Lock()


def create_app() -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.secret_key = os.environ.get("APP_SECRET_KEY", "dev-secret-key")

    instance_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "instance")
    os.makedirs(instance_dir, exist_ok=True)
    db_path = os.path.join(instance_dir, "app.db")

    app.config.update(
        SQLALCHEMY_DATABASE_URI=f"sqlite:///{db_path}",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        JSON_AS_ASCII=False,
    )

    db.init_app(app)

    with app.app_context():
        db.create_all()
        seed_defaults()

    @app.before_request
    def require_login() -> Optional[Response]:
        public_paths = {"/login", "/healthz", "/static"}
        if request.path == "/" or request.path.startswith(tuple(public_paths)):
            return None
        if session.get("user_id") is None:
            return redirect(url_for("login", next=request.path))
        return None

    @app.get("/healthz")
    def healthz() -> Response:
        return jsonify({"ok": True, "ts": as_iso(utc_now())})

    @app.get("/")
    def index() -> Response:
        return redirect(url_for("bigscreen"))

    @app.route("/login", methods=["GET", "POST"])
    def login() -> Response:
        if request.method == "POST":
            username = (request.form.get("username") or "").strip()
            password = request.form.get("password") or ""
            user = User.query.filter_by(username=username).first()
            if user and check_password_hash(user.password_hash, password):
                session["user_id"] = user.id
                next_url = request.args.get("next") or url_for("bigscreen")
                return redirect(next_url)
            flash("账号或密码错误", "error")
        return render_template("login.html")

    @app.post("/logout")
    def logout() -> Response:
        session.clear()
        return redirect(url_for("login"))

    @app.get("/admin")
    def admin() -> Response:
        return render_template("admin.html")

    @app.get("/crawlers")
    def crawlers() -> Response:
        sources = CrawlerSource.query.order_by(CrawlerSource.id.desc()).all()
        return render_template("crawlers.html", sources=sources)

    @app.post("/crawlers")
    def crawlers_create() -> Response:
        name = (request.form.get("name") or "").strip()
        type_ = (request.form.get("type") or "baidu").strip()
        enabled = request.form.get("enabled") == "on"
        if not name:
            flash("爬虫源名称不能为空", "error")
            return redirect(url_for("crawlers"))
        src = CrawlerSource(name=name, type=type_, enabled=enabled, config_json="{}")
        db.session.add(src)
        db.session.commit()
        return redirect(url_for("crawlers"))

    @app.post("/crawlers/<int:source_id>/toggle")
    def crawlers_toggle(source_id: int) -> Response:
        src = CrawlerSource.query.get_or_404(source_id)
        src.enabled = not src.enabled
        db.session.commit()
        return redirect(url_for("crawlers"))

    @app.post("/crawlers/<int:source_id>/delete")
    def crawlers_delete(source_id: int) -> Response:
        src = CrawlerSource.query.get_or_404(source_id)
        db.session.delete(src)
        db.session.commit()
        return redirect(url_for("crawlers"))

    @app.get("/collect")
    def collect_page() -> Response:
        sources = CrawlerSource.query.order_by(CrawlerSource.id.desc()).all()
        return render_template("collect.html", sources=sources)

    @app.post("/api/collect/start")
    def api_collect_start() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        keyword = (payload.get("keyword") or "").strip()
        source_ids = payload.get("source_ids") or []
        limit = payload.get("limit")
        if not keyword:
            return jsonify({"ok": False, "error": "关键字不能为空"}), 400
        try:
            limit_int = int(limit) if limit is not None else 10
        except Exception:
            limit_int = 10
        limit_int = max(1, min(50, limit_int))

        enabled_sources = (
            CrawlerSource.query.filter(CrawlerSource.enabled.is_(True))
            .filter(CrawlerSource.id.in_(source_ids) if source_ids else True)
            .all()
        )
        if not enabled_sources:
            return jsonify({"ok": False, "error": "未选择可用爬虫源"}), 400

        record_keyword_search(keyword)

        channel_id = collect_stream.create_channel()
        threading.Thread(
            target=run_collect_job,
            args=(app, channel_id, keyword, enabled_sources, limit_int),
            daemon=True,
        ).start()
        return jsonify({"ok": True, "channel_id": channel_id})

    @app.get("/api/collect/stream/<channel_id>")
    def api_collect_stream(channel_id: str) -> Response:
        def sse() -> Iterable[str]:
            yield "event: ready\ndata: {}\n\n"
            for event in collect_stream.events(channel_id):
                if event.event == "close":
                    yield "event: close\ndata: {}\n\n"
                    break
                yield f"event: {event.event}\n"
                yield "data: " + json.dumps(event.data, ensure_ascii=False) + "\n\n"

        return Response(sse(), mimetype="text/event-stream")

    @app.post("/api/items/save")
    def api_items_save() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        item_ids = payload.get("item_ids") or []
        if not item_ids:
            return jsonify({"ok": False, "error": "未选择数据"}), 400
        updated = (
            CollectedItem.query.filter(CollectedItem.id.in_(item_ids)).update(
                {CollectedItem.saved: True}, synchronize_session=False
            )
            or 0
        )
        db.session.commit()
        return jsonify({"ok": True, "saved": int(updated)})

    @app.get("/data")
    def data_page() -> Response:
        q = (request.args.get("q") or "").strip()
        page = int(request.args.get("page") or 1)
        page_size = 12
        base = CollectedItem.query.filter(CollectedItem.saved.is_(True))
        if q:
            like = f"%{q}%"
            base = base.filter((CollectedItem.title.like(like)) | (CollectedItem.source.like(like)))
        total = base.count()
        items = (
            base.order_by(CollectedItem.collected_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .all()
        )
        return render_template(
            "data.html",
            items=items,
            q=q,
            page=page,
            page_size=page_size,
            total=total,
        )

    @app.post("/data/delete")
    def data_delete() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        item_ids = payload.get("item_ids") or []
        if not item_ids:
            return jsonify({"ok": False, "error": "未选择数据"}), 400
        deleted = (
            CollectedItem.query.filter(CollectedItem.id.in_(item_ids)).delete(synchronize_session=False)
            or 0
        )
        db.session.commit()
        return jsonify({"ok": True, "deleted": int(deleted)})

    @app.get("/models")
    def models_page() -> Response:
        configs = ModelConfig.query.order_by(ModelConfig.id.desc()).all()
        total_tokens = db.session.query(func.coalesce(func.sum(TokenUsage.total_tokens), 0)).scalar() or 0
        return render_template("models.html", configs=configs, total_tokens=int(total_tokens))

    @app.post("/models")
    def models_upsert() -> Response:
        payload = request.form
        config_id = payload.get("id")
        name = (payload.get("name") or "default").strip()
        base_url = (payload.get("base_url") or "").strip()
        api_key = (payload.get("api_key") or "").strip()
        model = (payload.get("model") or "").strip()
        system_prompt = payload.get("system_prompt") or ""
        enabled = payload.get("enabled") == "on"

        if config_id:
            cfg = ModelConfig.query.get_or_404(int(config_id))
        else:
            cfg = ModelConfig()
            db.session.add(cfg)

        cfg.name = name or "default"
        cfg.base_url = base_url or "https://api.openai.com/v1"
        cfg.api_key = api_key
        cfg.model = model or "gpt-4o-mini"
        cfg.system_prompt = system_prompt or "你是政企舆情分析助手。"
        cfg.enabled = enabled
        db.session.commit()
        return redirect(url_for("models_page"))

    @app.post("/models/<int:config_id>/toggle")
    def models_toggle(config_id: int) -> Response:
        cfg = ModelConfig.query.get_or_404(config_id)
        cfg.enabled = not cfg.enabled
        db.session.commit()
        return redirect(url_for("models_page"))

    @app.post("/models/<int:config_id>/delete")
    def models_delete(config_id: int) -> Response:
        cfg = ModelConfig.query.get_or_404(config_id)
        TokenUsage.query.filter(TokenUsage.model_config_id == config_id).update(
            {TokenUsage.model_config_id: None},
            synchronize_session=False,
        )
        db.session.delete(cfg)
        db.session.commit()
        return redirect(url_for("models_page"))

    @app.post("/api/models/test")
    def api_models_test() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        config_id = payload.get("config_id")
        message = (payload.get("message") or "你好").strip()
        cfg = None
        if config_id is not None:
            cfg = ModelConfig.query.get(int(config_id))
        if cfg is None:
            cfg = ModelConfig.query.filter(ModelConfig.enabled.is_(True)).order_by(ModelConfig.id.desc()).first()
        if cfg is None or not cfg.api_key:
            return jsonify({"ok": False, "error": "未配置可用模型"}), 400

        try:
            reply, usage = call_openai_chat(cfg, message)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        if usage:
            db.session.add(
                TokenUsage(
                    model_config_id=cfg.id,
                    input_tokens=int(usage.get("prompt_tokens") or 0),
                    output_tokens=int(usage.get("completion_tokens") or 0),
                    total_tokens=int(usage.get("total_tokens") or 0),
                )
            )
            db.session.commit()

        return jsonify({"ok": True, "reply": reply})

    @app.get("/report")
    def report_page() -> Response:
        return render_template("report.html", body_class="reportPageBody")

    @app.get("/api/report/overview")
    def api_report_overview() -> Response:
        saved_total = int(CollectedItem.query.filter(CollectedItem.saved.is_(True)).count() or 0)

        saved_sources_total = (
            db.session.query(func.count(func.distinct(CollectedItem.source)))
            .filter(CollectedItem.saved.is_(True))
            .scalar()
            or 0
        )
        saved_keywords_total = (
            db.session.query(func.count(func.distinct(CollectedItem.keyword)))
            .filter(CollectedItem.saved.is_(True))
            .filter(CollectedItem.keyword != "")
            .scalar()
            or 0
        )

        top_sources = (
            db.session.query(CollectedItem.source, func.count(CollectedItem.id))
            .filter(CollectedItem.saved.is_(True))
            .group_by(CollectedItem.source)
            .order_by(func.count(CollectedItem.id).desc())
            .limit(10)
            .all()
        )
        top_keywords = (
            db.session.query(CollectedItem.keyword, func.count(CollectedItem.id))
            .filter(CollectedItem.saved.is_(True))
            .filter(CollectedItem.keyword != "")
            .group_by(CollectedItem.keyword)
            .order_by(func.count(CollectedItem.id).desc())
            .limit(12)
            .all()
        )
        recent_searches = (
            KeywordSearch.query.order_by(KeywordSearch.last_searched_at.desc()).limit(20).all()
        )
        top_searches = KeywordSearch.query.order_by(KeywordSearch.search_count.desc()).limit(12).all()

        return jsonify(
            {
                "ok": True,
                "saved_total": saved_total,
                "saved_sources_total": int(saved_sources_total),
                "saved_keywords_total": int(saved_keywords_total),
                "top_sources": [{"name": s, "value": int(c)} for (s, c) in top_sources],
                "top_keywords": [{"keyword": k, "value": int(c)} for (k, c) in top_keywords if k],
                "recent_searches": [
                    {
                        "keyword": x.keyword,
                        "count": int(x.search_count),
                        "last": as_iso(x.last_searched_at),
                    }
                    for x in recent_searches
                ],
                "top_searches": [
                    {"keyword": x.keyword, "count": int(x.search_count)} for x in top_searches
                ],
            }
        )

    @app.post("/api/report/analyze")
    def api_report_analyze() -> Response:
        payload = request.get_json(force=True, silent=True) or {}
        text = (payload.get("text") or "").strip()
        if not text:
            return jsonify({"ok": False, "error": "请输入要分析的文本"}), 400

        cfg = ModelConfig.query.filter(ModelConfig.enabled.is_(True)).order_by(ModelConfig.id.desc()).first()
        if cfg and cfg.api_key:
            try:
                reply, usage = call_openai_report(cfg, text)
                if usage:
                    db.session.add(
                        TokenUsage(
                            model_config_id=cfg.id,
                            input_tokens=int(usage.get("prompt_tokens") or 0),
                            output_tokens=int(usage.get("completion_tokens") or 0),
                            total_tokens=int(usage.get("total_tokens") or 0),
                        )
                    )
                    db.session.commit()
                return jsonify({"ok": True, "report": reply, "mode": "ai"})
            except Exception:
                pass

        report = local_analyze(text)
        return jsonify({"ok": True, "report": report, "mode": "local"})

    @app.get("/bigscreen")
    def bigscreen() -> Response:
        return render_template("bigscreen.html")

    @app.get("/api/bigscreen/summary")
    def api_bigscreen_summary() -> Response:
        payload = get_baidu_hot_payload()
        if payload is not None:
            return jsonify(payload)

        hot = (
            CollectedItem.query.filter(CollectedItem.saved.is_(True))
            .order_by(CollectedItem.collected_at.desc())
            .limit(12)
            .all()
        )
        by_source = (
            db.session.query(CollectedItem.source, func.count(CollectedItem.id))
            .filter(CollectedItem.saved.is_(True))
            .group_by(CollectedItem.source)
            .order_by(func.count(CollectedItem.id).desc())
            .limit(8)
            .all()
        )
        return jsonify(
            {
                "ok": True,
                "mode": "db",
                "hot": [item_to_dict(x) for x in hot],
                "sources": [{"name": s, "value": int(c)} for (s, c) in by_source],
            }
        )

    return app


def seed_defaults() -> None:
    if User.query.count() == 0:
        db.session.add(
            User(
                username="admin",
                password_hash=generate_password_hash("admin123"),
            )
        )
        db.session.commit()

    ensure_crawler_source(name="Google 新闻 RSS", type_="google_news_rss", enabled=True)
    ensure_crawler_source(name="GDELT 全球媒体", type_="gdelt", enabled=True)
    ensure_crawler_source(name="百度搜索", type_="baidu", enabled=False)

    if ModelConfig.query.count() == 0:
        db.session.add(ModelConfig(name="默认模型", enabled=False))
        db.session.commit()


def item_to_dict(item: CollectedItem) -> Dict[str, Any]:
    return {
        "id": item.id,
        "title": item.title,
        "url": item.url,
        "source": item.source,
        "cover_url": item.cover_url,
        "published_at": as_iso(item.published_at) if item.published_at else None,
        "collected_at": as_iso(item.collected_at),
        "keyword": item.keyword,
        "saved": bool(item.saved),
    }


def normalize_url(url: str) -> str:
    return url.strip()


def fetch_baidu_hot(limit: int = 12, tab: str = "realtime") -> List[Dict[str, Any]]:
    api_url = "https://top.baidu.com/api/board"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://top.baidu.com/",
    }
    params = {"platform": "wise", "tab": tab}
    r = requests.get(api_url, params=params, headers=headers, timeout=12)
    r.raise_for_status()
    try:
        data = r.json() if r.text else {}
    except Exception:
        data = {}
    if not isinstance(data, dict) or not data.get("success"):
        return []
    root = (data.get("data") or {}) if isinstance(data.get("data"), dict) else {}

    items: List[Dict[str, Any]] = []
    seen: set[str] = set()

    def walk(x: Any) -> None:
        if isinstance(x, dict):
            if "word" in x and "url" in x:
                word_raw = x.get("word")
                href_raw = x.get("url")
                word = str(word_raw).strip() if word_raw is not None else ""
                href = str(href_raw).strip() if href_raw is not None else ""
                if word and href and word not in seen:
                    tag_raw = x.get("labelTagName") or x.get("newHotName") or "百度热搜"
                    tag = tag_raw.strip() if isinstance(tag_raw, str) else "百度热搜"
                    items.append({"title": word[:500], "url": href, "source": tag[:128]})
                    seen.add(word)
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(root)

    now = as_iso(utc_now())
    out: List[Dict[str, Any]] = []
    for it in items[: max(1, int(limit or 12))]:
        href = (it.get("url") or "").strip()
        if href.startswith("//"):
            href = "https:" + href
        elif href.startswith("/"):
            href = urljoin("https://www.baidu.com/", href)
        out.append(
            {
                "title": it.get("title") or "",
                "url": href,
                "source": it.get("source") or "百度热搜",
                "collected_at": now,
            }
        )
    return out


def get_baidu_hot_payload() -> Optional[Dict[str, Any]]:
    now_ts = time.time()
    with _baidu_hot_cache_lock:
        ts = float(_baidu_hot_cache.get("ts") or 0.0)
        payload = _baidu_hot_cache.get("payload")
        if payload is not None and now_ts - ts < 30:
            return payload

    try:
        hot = fetch_baidu_hot(limit=30, tab="realtime")
    except Exception:
        hot = []

    if not hot:
        return None

    counts: Dict[str, int] = {}
    for x in hot:
        k = (x.get("source") or "").strip() or "百度热搜"
        counts[k] = counts.get(k, 0) + 1
    sources = [{"name": k, "value": int(v)} for (k, v) in sorted(counts.items(), key=lambda t: (-t[1], t[0]))][
        :8
    ]
    payload = {
        "ok": True,
        "mode": "baidu_hot",
        "ts": as_iso(utc_now()),
        "hot": hot,
        "sources": sources,
    }
    with _baidu_hot_cache_lock:
        _baidu_hot_cache["ts"] = now_ts
        _baidu_hot_cache["payload"] = payload
    return payload


def parse_baidu_results(html: str) -> List[Dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    results: List[Dict[str, Any]] = []
    for block in soup.select("div.result, div.result-op"):
        a = block.select_one("h3 a")
        if not a:
            continue
        title = a.get_text(" ", strip=True)
        href = a.get("href") or ""
        href = normalize_url(href)
        source = "百度"
        time_text = ""
        span = block.select_one(".c-abstract, .content-right_8Zs40, .c-color-gray2")
        if span:
            time_text = span.get_text(" ", strip=True)
        results.append(
            {
                "title": title[:500],
                "url": href,
                "source": source,
                "published_at": parse_time_guess(time_text),
                "cover_url": None,
            }
        )
        if len(results) >= 20:
            break
    return results


def parse_time_guess(text: str) -> Optional[datetime]:
    if not text:
        return None
    m = re.search(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return datetime(y, mo, d, tzinfo=timezone.utc)
    return None


def run_collect_job(
    app: Flask,
    channel_id: str,
    keyword: str,
    sources: List[CrawlerSource],
    limit: int,
) -> None:
    with app.app_context():
        collect_stream.put(channel_id, StreamEvent(event="status", data={"status": "running"}))
        emitted = 0
        remaining = max(0, int(limit or 0))
        try:
            for src in sources:
                if remaining <= 0:
                    break
                try:
                    if src.type == "baidu":
                        n = stream_items(app, channel_id, keyword, baidu_search(keyword), remaining)
                        emitted += n
                        remaining -= n
                    elif src.type == "google_news_rss":
                        n = stream_items(app, channel_id, keyword, google_news_rss_search(keyword), remaining)
                        emitted += n
                        remaining -= n
                    elif src.type == "gdelt":
                        n = stream_items(app, channel_id, keyword, gdelt_search(keyword), remaining)
                        emitted += n
                        remaining -= n
                except Exception:
                    continue
        finally:
            if emitted == 0:
                collect_stream.put(
                    channel_id,
                    StreamEvent(
                        event="status",
                        data={
                            "status": "empty",
                            "message": "未获取到真实数据，请更换关键字或切换数据源。",
                        },
                    ),
                )
            collect_stream.put(channel_id, StreamEvent(event="status", data={"status": "done"}))
            collect_stream.close(channel_id)


def baidu_search(keyword: str) -> List[Dict[str, Any]]:
    url = "https://www.baidu.com/s"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    params = {"wd": keyword}
    try:
        r = requests.get(url, params=params, headers=headers, timeout=12)
        r.raise_for_status()
        html = r.text
        parsed = parse_baidu_results(html)
        if parsed:
            return parsed
    except Exception:
        return []
    return []


def google_news_rss_search(keyword: str) -> List[Dict[str, Any]]:
    q = quote_plus(keyword)
    url = f"https://news.google.com/rss/search?q={q}&hl=zh-CN&gl=CN&ceid=CN:zh-Hans"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    r = requests.get(url, headers=headers, timeout=12)
    r.raise_for_status()
    return parse_rss_items(r.text, default_source="Google 新闻")


def parse_rss_items(xml_text: str, default_source: str) -> List[Dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: List[Dict[str, Any]] = []

    def text_of(el: Optional[ET.Element], tag: str) -> str:
        if el is None:
            return ""
        node = el.find(tag)
        return (node.text or "").strip() if node is not None and node.text else ""

    def first_attr(el: Optional[ET.Element], xpath: str, attr: str) -> Optional[str]:
        if el is None:
            return None
        node = el.find(xpath)
        if node is None:
            return None
        val = node.attrib.get(attr)
        return val.strip() if val else None

    channel = root.find("channel")
    if channel is None:
        channel = root

    for it in channel.findall(".//item"):
        title = text_of(it, "title")
        link = text_of(it, "link")
        pub = text_of(it, "pubDate")
        published_at: Optional[datetime] = None
        if pub:
            try:
                published_at = parsedate_to_datetime(pub).astimezone(timezone.utc)
            except Exception:
                published_at = None

        source = text_of(it, "source") or default_source
        desc = text_of(it, "description")
        cover = (
            first_attr(it, "{http://search.yahoo.com/mrss/}content", "url")
            or first_attr(it, "enclosure", "url")
            or extract_first_img_from_html(desc, base_url=link)
        )

        if not title or not link:
            continue
        items.append(
            {
                "title": title[:500],
                "url": link[:2048],
                "source": source[:128],
                "published_at": published_at,
                "cover_url": cover,
            }
        )
        if len(items) >= 25:
            break
    return items


def gdelt_search(keyword: str) -> List[Dict[str, Any]]:
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    params = {
        "query": keyword,
        "mode": "ArtList",
        "format": "json",
        "maxrecords": 25,
        "sort": "HybridRel",
    }
    r = requests.get(url, params=params, headers=headers, timeout=15)
    r.raise_for_status()
    data = r.json()
    articles = data.get("articles") or []
    items: List[Dict[str, Any]] = []
    for a in articles:
        title = (a.get("title") or "").strip()
        link = (a.get("url") or "").strip()
        if not title or not link:
            continue
        source = (a.get("domain") or a.get("sourceCountry") or "GDELT").strip()
        cover = (a.get("socialimage") or a.get("image")) if isinstance(a, dict) else None
        seendate = (a.get("seendate") or "").strip()
        published_at: Optional[datetime] = None
        if seendate:
            try:
                published_at = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except Exception:
                published_at = None
        items.append(
            {
                "title": title[:500],
                "url": link[:2048],
                "source": source[:128],
                "published_at": published_at,
                "cover_url": cover,
            }
        )
        if len(items) >= 25:
            break
    return items


def stream_items(
    app: Flask,
    channel_id: str,
    keyword: str,
    items: List[Dict[str, Any]],
    limit: int,
) -> int:
    emitted = 0
    for item in items:
        if emitted >= limit:
            break
        if not item.get("cover_url") and item.get("url"):
            item["cover_url"] = cached_fetch_cover_url(str(item.get("url")))
        saved_item = upsert_collected_item(keyword=keyword, item=item)
        collect_stream.put(
            channel_id,
            StreamEvent(event="item", data={"item": item_to_dict(saved_item)}),
        )
        emitted += 1
        time.sleep(0.12)
    return emitted


def ensure_crawler_source(name: str, type_: str, enabled: bool) -> None:
    exists = CrawlerSource.query.filter_by(type=type_).first()
    if exists is not None:
        return
    db.session.add(CrawlerSource(name=name, type=type_, enabled=enabled, config_json="{}"))
    db.session.commit()


def record_keyword_search(keyword: str) -> None:
    k = (keyword or "").strip()[:128]
    if not k:
        return
    row = KeywordSearch.query.filter_by(keyword=k).first()
    if row is None:
        row = KeywordSearch(keyword=k, search_count=0)
        db.session.add(row)
    row.search_count = int(row.search_count or 0) + 1
    row.last_searched_at = utc_now()
    db.session.commit()


def extract_first_img_from_html(html: str, base_url: str) -> Optional[str]:
    if not html:
        return None
    try:
        soup = BeautifulSoup(html, "html.parser")
        img = soup.find("img")
        if not img:
            return None
        src = img.get("src") or ""
        return normalize_cover_url(src, base_url=base_url)
    except Exception:
        return None


def normalize_cover_url(url: str, base_url: str) -> Optional[str]:
    u = (url or "").strip()
    if not u:
        return None
    if u.startswith("//"):
        u = "https:" + u
    if u.startswith("/"):
        u = urljoin(base_url, u)
    if not (u.startswith("http://") or u.startswith("https://")):
        return None
    return u


def cached_fetch_cover_url(url: str) -> Optional[str]:
    with _cover_cache_lock:
        if url in _cover_cache:
            return _cover_cache[url]
    cover = fetch_cover_url(url)
    with _cover_cache_lock:
        if len(_cover_cache) > 800:
            _cover_cache.clear()
        _cover_cache[url] = cover
    return cover


def fetch_cover_url(url: str) -> Optional[str]:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }
    try:
        r = requests.get(url, headers=headers, timeout=10, allow_redirects=True)
        r.raise_for_status()
    except Exception:
        return None

    html = r.text or ""
    if not html:
        return None

    try:
        soup = BeautifulSoup(html, "html.parser")
        candidates: List[Optional[str]] = [
            soup.find("meta", attrs={"property": "og:image"}) and soup.find("meta", attrs={"property": "og:image"}).get("content"),
            soup.find("meta", attrs={"name": "twitter:image"}) and soup.find("meta", attrs={"name": "twitter:image"}).get("content"),
            soup.find("meta", attrs={"property": "twitter:image"}) and soup.find("meta", attrs={"property": "twitter:image"}).get("content"),
            soup.find("link", attrs={"rel": "image_src"}) and soup.find("link", attrs={"rel": "image_src"}).get("href"),
        ]
        for c in candidates:
            n = normalize_cover_url(str(c or ""), base_url=r.url)
            if n:
                return n
        img = soup.find("img")
        if img:
            n = normalize_cover_url(str(img.get("src") or ""), base_url=r.url)
            if n:
                return n
    except Exception:
        return None
    return None


def upsert_collected_item(keyword: str, item: Dict[str, Any]) -> CollectedItem:
    external_id = uuid.uuid5(uuid.NAMESPACE_URL, item.get("url") or uuid.uuid4().hex).hex
    row = CollectedItem.query.filter_by(external_id=external_id).first()
    if row is None:
        row = CollectedItem(external_id=external_id)
        db.session.add(row)

    row.title = (item.get("title") or "").strip()[:500]
    row.url = (item.get("url") or "").strip()[:2048]
    row.source = (item.get("source") or "unknown").strip()[:128]
    row.cover_url = (item.get("cover_url") or "").strip()[:2048] or None
    row.published_at = item.get("published_at")
    row.keyword = keyword[:128]
    row.collected_at = utc_now()
    db.session.commit()
    return row


def call_openai_chat(cfg: ModelConfig, user_message: str) -> Tuple[str, Dict[str, Any]]:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": cfg.system_prompt},
            {"role": "user", "content": user_message},
        ],
        temperature=0.2,
    )
    text = (resp.choices[0].message.content or "").strip()
    usage = {}
    if getattr(resp, "usage", None):
        usage = {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
            "total_tokens": getattr(resp.usage, "total_tokens", 0),
        }
    return text, usage


def call_openai_report(cfg: ModelConfig, text: str) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    from openai import OpenAI

    client = OpenAI(api_key=cfg.api_key, base_url=cfg.base_url)
    prompt = (
        "请对下列文本生成结构化政企舆情分析报告，严格输出JSON对象，字段包含："
        "summary(字符串)、sentiment(正面/负面/中性)、topics(字符串数组)、keywords(字符串数组)。\n\n"
        f"文本：{text}"
    )
    resp = client.chat.completions.create(
        model=cfg.model,
        messages=[
            {"role": "system", "content": cfg.system_prompt},
            {"role": "user", "content": prompt},
        ],
        temperature=0.2,
        response_format={"type": "json_object"},
    )
    raw = (resp.choices[0].message.content or "").strip()
    report = json.loads(raw)
    usage = {}
    if getattr(resp, "usage", None):
        usage = {
            "prompt_tokens": getattr(resp.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(resp.usage, "completion_tokens", 0),
            "total_tokens": getattr(resp.usage, "total_tokens", 0),
        }
    return report, usage


def local_analyze(text: str) -> Dict[str, Any]:
    summary = text[:160].strip()
    sentiment = local_sentiment(text)
    keywords = extract_keywords(text, top_k=8)
    topics = keywords[:4]
    return {
        "summary": summary,
        "sentiment": sentiment,
        "topics": topics,
        "keywords": keywords,
    }


def local_sentiment(text: str) -> str:
    positive = ["利好", "增长", "提升", "获批", "推进", "改善", "成功", "满意"]
    negative = ["投诉", "舆情", "风险", "下滑", "事故", "处罚", "违规", "失信", "负面"]
    score = 0
    for w in positive:
        if w in text:
            score += 1
    for w in negative:
        if w in text:
            score -= 1
    if score >= 2:
        return "正面"
    if score <= -2:
        return "负面"
    return "中性"


def extract_keywords(text: str, top_k: int = 8) -> List[str]:
    tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z]{3,}", text)
    counts: Dict[str, int] = {}
    stop = {
        "我们",
        "你们",
        "他们",
        "以及",
        "对于",
        "因为",
        "所以",
        "这个",
        "那个",
        "进行",
        "相关",
        "目前",
        "今日",
        "其中",
        "公司",
        "政府",
        "部门",
    }
    for t in tokens:
        if t in stop:
            continue
        counts[t] = counts.get(t, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [k for (k, _) in ranked[:top_k]]


if __name__ == "__main__":
    app = create_app()
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), debug=True)
