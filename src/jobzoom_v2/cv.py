from __future__ import annotations
import re
import shutil
import subprocess
import unicodedata
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer


NAVY = colors.HexColor("#153652")


def ascii_text(value: str) -> str:
    value = str(value).replace("–", "-").replace("—", "-").replace("→", "to")
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def parse_master(path: Path) -> dict:
    lines = Path(path).read_text().splitlines()
    sections = {}
    current = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            sections[current] = []
        elif current:
            sections[current].append(line)
    return sections


def bullet_pool(sections: dict) -> set[str]:
    return {
        line[2:].strip()
        for lines in sections.values()
        for line in lines
        if line.startswith("- ")
    }


def executive_summary(sections: dict) -> str:
    return " ".join(
        line.strip() for line in sections.get("Executive Summary", [])
        if line.strip() and not line.startswith("---")
    )


def contact_line(sections: dict) -> str:
    values = {}
    for line in sections.get("Contact", []):
        match = re.match(r"- \*\*(.+?):\*\*\s*(.+)", line.strip())
        if match:
            values[match.group(1)] = match.group(2)
    return " | ".join(
        values.get(key, "")
        for key in ("Location", "Phone", "Email", "LinkedIn")
        if values.get(key)
    )


def render(master_path: Path, candidate_name: str, target_role: str, highlights: list[str], output: Path):
    sections = parse_master(master_path)
    allowed = bullet_pool(sections)
    if any(item not in allowed for item in highlights):
        raise ValueError("highlight_not_in_master_cv")
    output = Path(output)
    if output.exists():
        raise RuntimeError("refusing_to_overwrite_cv")
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(
        name="CVBody", fontName="Helvetica", fontSize=8.4,
        leading=10.5, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="CVSmall", fontName="Helvetica", fontSize=7.6,
        leading=9.2, spaceAfter=2,
    ))
    styles["Title"].textColor = NAVY
    styles["Heading2"].textColor = NAVY
    story = [
        Paragraph(escape(ascii_text(candidate_name)), styles["Title"]),
        Paragraph(escape(ascii_text(target_role)), styles["Heading2"]),
        Paragraph(
            escape(ascii_text(contact_line(sections))),
            styles["CVSmall"],
        ),
        Spacer(1, 4),
        Paragraph("EXECUTIVE SUMMARY", styles["Heading2"]),
        Paragraph(escape(ascii_text(executive_summary(sections))), styles["CVBody"]),
        Paragraph("CORE COMPETENCIES", styles["Heading2"]),
    ]
    competencies = [
        line[2:].strip()
        for line in sections.get("Core Competencies", [])
        if line.startswith("- ")
    ]
    story.append(Paragraph(
        escape(ascii_text(" | ".join(competencies))),
        styles["CVBody"],
    ))
    if highlights:
        story.append(Paragraph("SELECTED RELEVANT HIGHLIGHTS", styles["Heading2"]))
        for item in highlights:
            story.append(Paragraph("- " + escape(ascii_text(item)), styles["CVBody"]))
    story.append(Paragraph("PROFESSIONAL EXPERIENCE", styles["Heading2"]))
    for line in sections.get("Professional Experience", []):
        clean = line.strip()
        if not clean or clean == "---":
            continue
        if clean.startswith("### "):
            story.append(Spacer(1, 3))
            story.append(Paragraph(escape(ascii_text(clean[4:])), styles["Heading2"]))
        elif clean.startswith("**") and clean.endswith("**"):
            story.append(Paragraph(
                escape(ascii_text(clean.strip("*"))), styles["CVSmall"]
            ))
        elif clean.startswith("- "):
            story.append(Paragraph(
                "- " + escape(ascii_text(clean[2:])), styles["CVBody"]
            ))
    for section in ("Education", "Professional Certifications"):
        story.append(Spacer(1, 4))
        story.append(Paragraph(section.upper(), styles["Heading2"]))
        for line in sections.get(section, []):
            clean = line.strip()
            if not clean or clean == "---":
                continue
            if clean.startswith("### "):
                story.append(Paragraph(
                    escape(ascii_text(clean[4:])), styles["CVBody"]
                ))
            elif clean.startswith("**") and clean.endswith("**"):
                story.append(Paragraph(
                    escape(ascii_text(clean.strip("*"))), styles["CVSmall"]
                ))
            elif clean.startswith("- "):
                story.append(Paragraph(
                    "- " + escape(ascii_text(clean[2:])), styles["CVBody"]
                ))
    doc = SimpleDocTemplate(
        str(output), pagesize=A4,
        leftMargin=34, rightMargin=34, topMargin=30, bottomMargin=30,
    )
    doc.build(story)
    pdfinfo = shutil.which("pdfinfo")
    if pdfinfo:
        info = subprocess.run(
            [pdfinfo, str(output)],
            capture_output=True, text=True, timeout=10, check=True,
        )
        match = re.search(r"^Pages:\s+([0-9]+)$", info.stdout, re.MULTILINE)
        if not match or not 1 <= int(match.group(1)) <= 3:
            raise ValueError("cv_page_count_out_of_bounds")
    return output


def select_highlights(master_path: Path, job: dict, limit: int = 8) -> list[str]:
    sections = parse_master(master_path)
    bullets = sorted(bullet_pool(sections))
    target = ascii_text(
        str(job.get("title") or "") + " " + str(job.get("description") or "")
    ).lower()
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "your", "you",
        "will", "our", "are", "job", "role", "team", "into", "across", "lead",
    }
    tokens = {
        token for token in re.findall(r"[a-z0-9]+", target)
        if len(token) >= 4 and token not in stop
    }
    ranked = []
    for bullet in bullets:
        words = set(re.findall(r"[a-z0-9]+", ascii_text(bullet).lower()))
        overlap = len(tokens & words)
        if overlap:
            ranked.append((-overlap, bullet))
    return [bullet for _, bullet in sorted(ranked)[:max(0, int(limit))]]
