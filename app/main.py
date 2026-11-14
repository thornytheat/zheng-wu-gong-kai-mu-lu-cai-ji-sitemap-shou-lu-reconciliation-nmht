"""crawlscope 路由：只做参数解析与渲染，核心规则在 urls/crawler/reconcile/scan。"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from . import export, scan as scan_mod, settings, store, urls

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(title=settings.APP_TITLE)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class ScanRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=500)
    max_pages: int = Field(settings.DEFAULT_MAX_PAGES, ge=1, le=settings.MAX_PAGES_LIMIT)
    policy: Literal["strict", "tolerant"] = settings.DEFAULT_POLICY


class NormalizeRequest(BaseModel):
    url: str = Field(..., min_length=1, max_length=1000)
    base: str | None = None


def _render(request: Request, template: str, active: str = "", status_code: int = 200, **context):
    context.setdefault("app_name", settings.APP_TITLE)
    context.setdefault("sites", store.list_sites())
    context.setdefault("default_target", settings.DEFAULT_SAMPLE_SITE_URL)
    context.setdefault("policies", settings.POLICIES)
    context.setdefault("default_max_pages", settings.DEFAULT_MAX_PAGES)
    context.setdefault("page", active)
    context.setdefault("scan_total", len(store.list_scans(limit=200)))
    context["active"] = active
    return templates.TemplateResponse(request, template, context, status_code=status_code)


def _overview(scans: list[dict]) -> dict:
    """首页指标：全部来自 scans 表，没有扫描时全 0（不伪造数据）。"""
    latest = scans[0] if scans else None
    if not latest:
        return {
            "scan_total": 0,
            "pages_fetched": 0,
            "catalog_count": 0,
            "diff_total": 0,
            "failure_total": 0,
            "latest_key": "",
        }
    return {
        "scan_total": len(scans),
        "pages_fetched": latest["pages_fetched"],
        "catalog_count": latest["catalog_count"],
        "diff_total": latest["only_catalog_count"] + latest["only_sitemap_count"] + latest["mismatch_count"],
        "failure_total": latest["failure_count"],
        "latest_key": latest["scan_key"],
    }


def _latest_scan(scans: list[dict]) -> dict | None:
    return store.get_scan(scans[0]["scan_key"]) if scans else None


@app.get("/health")
def health():
    return {"status": "ok", "app": settings.APP_NAME, "db": str(settings.db_path())}


@app.get("/", response_class=Response)
def index(request: Request):
    scans = store.list_scans(limit=200)
    return _render(
        request,
        "index.html",
        active="index",
        sample_site_url=settings.DEFAULT_SAMPLE_SITE_URL,
        recent=scans[:6],
        stats=_overview(scans),
        scan_total=len(scans),
    )


@app.get("/workbench", response_class=Response)
def workbench(request: Request):
    scans = store.list_scans(limit=10)
    return _render(
        request,
        "workbench.html",
        active="workbench",
        recent=scans,
        latest_scan=_latest_scan(scans),
    )


@app.post("/api/scan")
def api_scan(payload: ScanRequest):
    try:
        result = scan_mod.run_scan(payload.target, payload.max_pages, payload.policy)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    key = store.save_scan(result)
    return JSONResponse(
        {
            "scan_id": key,
            "result_url": "/result/%s" % key,
            "summary": result["summary"],
            "pagination": result["pagination"],
            "conclusion": result["diff"]["conclusion"],
        }
    )


@app.get("/api/scan/{scan_id}")
def api_scan_detail(scan_id: str):
    payload = store.get_scan(scan_id)
    if not payload:
        raise HTTPException(status_code=404, detail="未找到该扫描记录")
    return JSONResponse(payload)


@app.post("/api/normalize")
def api_normalize(payload: NormalizeRequest):
    try:
        normalized, changes = urls.normalize_with_evidence(payload.url, payload.base or None)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"normalized": normalized, "changes": changes}


@app.get("/result/{scan_id}", response_class=Response)
def result_page(request: Request, scan_id: str):
    payload = store.get_scan(scan_id)
    if not payload:
        scans = store.list_scans(limit=200)
        return _render(
            request,
            "index.html",
            active="index",
            status_code=404,
            error="未找到扫描记录 %s，可能已被清理，请到工作台重新执行采集。" % scan_id,
            sample_site_url=settings.DEFAULT_SAMPLE_SITE_URL,
            recent=scans[:6],
            stats=_overview(scans),
            scan_total=len(scans),
        )
    return _render(
        request,
        "result.html",
        active="workbench",
        scan=payload,
        csv_url="/review/%s/diff.csv" % scan_id,
    )


@app.get("/review", response_class=Response)
def review_page(request: Request):
    scans = store.list_scans(limit=50)
    totals = {
        "scans": len(scans),
        "diff": sum(row["only_catalog_count"] + row["only_sitemap_count"] + row["mismatch_count"] for row in scans),
        "failures": sum(row["failure_count"] for row in scans),
    }
    return _render(
        request,
        "review.html",
        active="review",
        scans=scans,
        totals=totals,
        latest_scan=_latest_scan(scans),
    )


@app.get("/review/{scan_id}/diff.csv")
def review_csv(scan_id: str):
    payload = store.get_scan(scan_id)
    if not payload:
        raise HTTPException(status_code=404, detail="未找到该扫描记录")
    body = export.to_csv(export.build_rows(payload))
    return Response(
        content=body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="crawlscope-%s-diff.csv"' % scan_id},
    )
