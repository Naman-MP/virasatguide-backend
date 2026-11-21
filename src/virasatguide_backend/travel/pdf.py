"""Printable itinerary cards, budget summary, and source appendix."""

from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

import reportlab
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

from .schemas import TripPlan

INK = colors.HexColor("#23332D")
RUST = colors.HexColor("#A44728")
CREAM = colors.HexColor("#F5F1E8")


def render_plan_pdf(plan: TripPlan) -> bytes:
    # ReportLab bundles Vera, so exported PDFs have consistent embedded fonts.
    font_dir = Path(reportlab.__file__).parent / "fonts"
    if "Virasat" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("Virasat", str(font_dir / "Vera.ttf")))
        pdfmetrics.registerFont(TTFont("VirasatBold", str(font_dir / "VeraBd.ttf")))
    body = ParagraphStyle("TravelBody", fontName="Virasat", fontSize=9, leading=14,
                          textColor=INK, spaceAfter=7, splitLongWords=True)
    small = ParagraphStyle("TravelSmall", parent=body, fontSize=8, leading=12)
    title = ParagraphStyle("TravelTitle", parent=body, fontName="VirasatBold", fontSize=25, leading=31, spaceAfter=13)
    heading = ParagraphStyle("TravelHeading", parent=body, fontName="VirasatBold", fontSize=14, leading=19, spaceBefore=15, spaceAfter=10)
    label = ParagraphStyle("TravelLabel", parent=small, fontName="VirasatBold", textColor=RUST)

    def p(text, style=body):
        return Paragraph(escape(str(text)), style)

    def money(amount):
        return f"{plan.request.currency} {amount:,}"

    def frame(canvas, doc):
        canvas.saveState()
        canvas.setFillColor(INK)
        canvas.rect(0, A4[1] - 40, A4[0], 40, fill=1, stroke=0)
        canvas.setFillColor(colors.white)
        canvas.setFont("VirasatBold", 10)
        canvas.drawString(42, A4[1] - 25, "VIRASAT / YOUR TRAVEL PLAN")
        canvas.setFillColor(INK)
        canvas.setFont("Virasat", 8)
        canvas.drawString(42, 25, "Generated itinerary / estimates subject to change")
        canvas.drawRightString(A4[0] - 42, 25, f"{doc.page}")
        canvas.restoreState()

    stream = BytesIO()
    doc = SimpleDocTemplate(stream, pagesize=A4, rightMargin=42, leftMargin=42,
                            topMargin=62, bottomMargin=48, title=plan.title, author="VirasatGuide")
    content = [p(plan.request.destination.upper(), label), p(plan.title, title), p(plan.summary)]
    location = " / ".join(v for v in [plan.request.area, plan.request.city] if v)
    if location:
        content.append(p(location, label))
    facts = [[p("DURATION", label), p("TRAVELERS", label), p("ESTIMATED TOTAL", label)],
             [p(f"{plan.request.days} day(s)"), p(plan.request.travelers), p(money(plan.estimated_total))]]
    table = Table(facts, colWidths=[145, 145, doc.width - 290], hAlign="LEFT")
    table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), CREAM),
                              ("TOPPADDING", (0, 0), (-1, -1), 8), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]))
    content += [Spacer(1, 10), table, Spacer(1, 9)]
    budget = "No budget specified" if plan.request.budget is None else f"Budget {money(plan.request.budget)} / {plan.budget_status.replace('_', ' ')}"
    content.append(p(budget, small))
    if plan.request.location:
        content.append(p(f"Search location: {plan.request.location.latitude}, {plan.request.location.longitude}. Distances unverified.", small))
    content.append(p("Costs cover all travelers. Stop costs, transport and accommodation are counted separately.", small))
    for day in plan.days:
        content.append(p(f"Day {day.day} - {day.title}", heading))
        for index, stop in enumerate(day.stops, 1):
            parts = [p(f"{stop.time_of_day.upper()} / {stop.duration_minutes} MIN / {money(stop.estimated_cost)}", label),
                     p(f"{index}. {stop.place_name}", ParagraphStyle("StopTitle", parent=body, fontName="VirasatBold", fontSize=11)),
                     p(stop.area, small), p(stop.description), p("Sources: " + ", ".join(stop.evidence_ids), small)]
            card = Table([[parts]], colWidths=[doc.width], hAlign="LEFT")
            card.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#DDD5C8")),
                                      ("LEFTPADDING", (0, 0), (-1, -1), 13), ("RIGHTPADDING", (0, 0), (-1, -1), 13),
                                      ("TOPPADDING", (0, 0), (-1, -1), 12), ("BOTTOMPADDING", (0, 0), (-1, -1), 7)]))
            content += [KeepTogether([card]), Spacer(1, 8)]
        content.append(p(f"Transport {money(day.transport_cost)} / Accommodation {money(day.accommodation_cost)} / Day total {money(day.estimated_total)}", label))
    if plan.tips:
        content.append(p("Before you go", heading))
        content.extend(p("- " + tip) for tip in plan.tips)
    content.append(p("Planning notes", heading))
    content.extend(p("- " + warning, small) for warning in plan.warnings)
    content.append(p("Sources & references", heading))
    for source in plan.sources:
        content.append(p(f"[{source.id}] {source.title}", label))
        content.append(p(source.text, small))
        if source.url:
            content.append(p(source.url, small))
    content.append(p(f"Created {plan.created_at.strftime('%d %b %Y')} / Plan {plan.plan_id}", small))
    doc.build(content, onFirstPage=frame, onLaterPages=frame)
    return stream.getvalue()
