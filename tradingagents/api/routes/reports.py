"""Report listing and retrieval endpoints."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class ReportInfo(BaseModel):
    id: str
    run_id: str
    ticker: str
    ticker_name: str | None = None
    rating: str | None = None
    report_path: str | None = None
    created_at: str


class ReportDetail(ReportInfo):
    content: str


@router.get("/reports", response_model=list[ReportInfo])
async def list_reports(request: Request, limit: int = 50, ticker: str | None = None):
    """List reports, optionally filtered by ticker."""
    db = request.app.state.db
    reports = db.list_reports(limit=limit, ticker=ticker)
    return [
        ReportInfo(
            id=r["id"],
            run_id=r["run_id"],
            ticker=r["ticker"],
            ticker_name=r.get("ticker_name"),
            rating=r.get("rating"),
            report_path=r.get("report_path"),
            created_at=r["created_at"],
        )
        for r in reports
    ]


@router.get("/reports/{report_id}", response_model=ReportDetail)
async def get_report(request: Request, report_id: str):
    """Get full report content."""
    db = request.app.state.db
    report = db.get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")
    return ReportDetail(
        id=report["id"],
        run_id=report["run_id"],
        ticker=report["ticker"],
        ticker_name=report.get("ticker_name"),
        rating=report.get("rating"),
        report_path=report.get("report_path"),
        created_at=report["created_at"],
        content=report["content"],
    )
