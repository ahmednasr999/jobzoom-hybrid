from __future__ import annotations
import re
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle


NAVY = colors.HexColor("#153652")
PALE = colors.HexColor("#edf3f8")
LINE = colors.HexColor("#dbe2ea")


def generate(run: dict, opportunities: list[dict], cv_rows: list[dict], output: Path):
    output = Path(output)
    if output.exists():
        raise RuntimeError("refusing_to_overwrite_report")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="BodyV2", fontName="Helvetica", fontSize=9.4,
        leading=12.5, spaceAfter=7,
    ))
    styles.add(ParagraphStyle(
        name="SmallV2", fontName="Helvetica", fontSize=7.8,
        leading=10, spaceAfter=5,
    ))
    styles["Title"].textColor = NAVY
    styles["Heading2"].textColor = NAVY


    def p(value, style="BodyV2"):
        return Paragraph(escape(str(value)), styles[style])

    def link(url):
        url = str(url or "")
        if not re.fullmatch(r"https://www\.linkedin\.com/jobs/view/[0-9]+/?", url):
            return p("Job link unavailable in saved evidence.", "SmallV2")
        return Paragraph(
            '<a color="#145d90" href="' + escape(url) + '">' +
            escape(url) + '</a>',
            styles["SmallV2"],
        )

    def table(rows):
        item = Table(
            [[p(a, "SmallV2"), p(b, "SmallV2")] for a, b in rows],
            colWidths=[355, 150],
            repeatRows=1,
        )
        item.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), PALE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LINEBELOW", (0, 0), (-1, -1), 0.3, LINE),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        return item

    readiness_threshold = int(run.get("readiness_threshold", 82))
    watchlist = sum(int(job.get("score", 0)) < readiness_threshold for job in opportunities)
    readiness = len(opportunities) - watchlist
    story = [
        p("JOBZOOM | PRIVATE DAILY REPORT", "SmallV2"),
        p("Daily opportunity review", "Title"),
        p(f"{run['run_date']} | {run.get('candidate_name', 'Candidate')}"),
        p(
            f"{len(opportunities)} included roles with verified CV references. "
            f"{watchlist} watchlist roles. {readiness} readiness-review roles. "
            "Compensation requires employer verification. "
            "No applications were submitted by this run."
        ),
        p("Exact processing funnel", "Heading2"),
    ]

    rows = [
        ("Gate", "Count"),
        ("Searches planned / completed / failed / pending",
         f"{run.get('planned_searches', 150)} / {run.get('completed_searches', 0)} / {run.get('failed_searches', 0)} / {run.get('pending_searches', 0)}"),
        ("Raw hits / unique jobs",
         f"{run.get('raw_hits', 0)} / {run.get('unique_jobs', 0)}"),
        ("Eligible for description / descriptions complete",
         f"{run.get('eligible_for_description', 0)} / {run.get('descriptions_complete', 0)}"),
        ("Validated scores / score-qualified",
         f"{run.get('scored_count', 0)} / {run.get('qualified_count', 0)}"),
        ("CVs generated / report roles",
         f"{len(cv_rows)} / {len(opportunities)}"),
    ]
    story.extend([
        table(rows),
        Spacer(1, 8),
        p(
            f"LinkedIn only. {run.get('title_count', 0)} target titles across "
            f"{run.get('country_count', 0)} configured countries. "
            f"Search window: {run.get('hours_old', 24)} hours. "
            "Search completion and suitable matches are separate outcomes.",
            "SmallV2",
        ),
        p(f"Run ID: {run['run_id']}", "SmallV2"),
        PageBreak(),
        p("Score-qualified opportunities", "Title"),
    ])

    if not opportunities:
        story.append(p("No score-qualified opportunities in this run."))
    by_id = {str(row["linkedin_id"]): row for row in cv_rows}
    for rank, job in enumerate(opportunities, 1):
        linkedin_id = str(job["linkedin_id"])
        cv = by_id.get(linkedin_id)
        if cv is None:
            raise ValueError("missing_cv_binding")
        band = "Readiness review" if int(job["score"]) >= readiness_threshold else "Watchlist"
        story.extend([
            p(f"{rank}. {job['title']}", "Heading2"),
            p(
                f"{job.get('company', '')} | {job.get('country', '')} | "
                f"Score {job['score']}/100 | {band}"
            ),
            p(job.get("rationale") or "No scoring explanation recorded."),
            p(
                "Configured compensation target: " +
                str(job.get("salary_floor") or "Not recorded") +
                ". This is a target, not an employer salary disclosure.",
                "SmallV2",
            ),
            link(job.get("url")),
            p(f"CV: {cv['filename']} | SHA-256: {cv['sha256']}", "SmallV2"),
            Spacer(1, 7),
        ])
    story.extend([
        PageBreak(),
        p("Evidence and next actions", "Title"),
        p(
            "Each listed role maps to one tailored CV. The ZIP manifest binds "
            "the report and CV bytes by SHA-256. Native delivery receipts are "
            "required before the run can become completed."
        ),

        p("Configured search coverage", "Heading2"),
        p(", ".join(run.get("countries", [])) or "No countries recorded."),
        p("Collection integrity", "Heading2"),
        p(
            "A provider restriction pauses collection. Completed query evidence "
            "remains durable. A paused or failed run cannot be reported as 150 of 150."
        ),
        p("Application status", "Heading2"),
        p(
            "Report delivery does not submit an application. Application recording "
            "remains a separate explicit action."
        ),
    ])
    doc = SimpleDocTemplate(
        str(output), pagesize=A4,
        rightMargin=42, leftMargin=42, topMargin=42, bottomMargin=42,
    )
    doc.build(story)
    return output
